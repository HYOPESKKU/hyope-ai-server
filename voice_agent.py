import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from clawops.agent import ClawOpsAgent, OpenAIRealtime

_QUESTIONS: dict = json.loads((Path(__file__).parent / "data" / "questions.json").read_text(encoding="utf-8"))

SYSTEM_PROMPT = (
    "You are a friendly AI phone assistant who checks in on the health of "
    "elderly residents. You are speaking aloud, so reply in one to three "
    "short conversational sentences — no markdown, no lists, never read raw "
    "JSON. "
    "Start the call with a warm greeting and a little natural small talk. "
    "Call the static_question tool to get the next required wellbeing "
    "question — never invent your own version of these questions or read "
    "raw field names aloud; rephrase each one naturally, as if you were "
    "just asking out of genuine care, and space them out across the call "
    "rather than asking them back to back like a survey. "
    "After the resident answers, judge for yourself whether the answer "
    "matches that question's negative_signal. If it does, call "
    "static_question again with the same category to get a deeper "
    "follow-up question in that same area before moving on to something "
    "else — do not skip ahead when a negative signal is detected. If it "
    "does not, call static_question again (with no category) whenever it "
    "feels natural to move on. "
    "Some questions come with a depends_on field naming another question "
    "and an expected answer. Only actually ask that question if the "
    "resident's earlier answer matched that condition; otherwise skip it "
    "silently and call static_question again to get a different question "
    "instead. "
    "Keep calling static_question and weaving its questions into the "
    "conversation until it returns done: true — at that point every "
    "required question has been covered, so let the resident's answer "
    "breathe with a short caring remark before continuing. "
    "If the resident says anything suggesting an emergency — a fall, "
    "injury, chest pain, confusion, or similar — respond with immediate "
    "concern and gently check on them, regardless of where you are in the "
    "questions. "
    "Once static_question returns done: true and the conversation has "
    "wound down naturally, say a warm goodbye."
)

# call_id → {recipient_id, guardian_phone, started_at, segments, progress}
_call_registry: dict[str, dict] = {}

# ClawOpsAgent가 session 인스턴스를 콜 간에 공유해서 한 번에 통화 하나만 처리한다.
# tool 함수는 clawops로부터 call 객체를 인자로 받지 못하므로, "지금 어떤 통화인지"를
# 여기 전역 변수로 추적해야 static_question이 해당 통화의 진행 상태를 찾을 수 있다.
_active_call_id: str | None = None

agent = ClawOpsAgent(
    from_=os.getenv("CLAWOPS_FROM_NUMBER", ""),
    session=OpenAIRealtime(
        system_prompt=SYSTEM_PROMPT,
        voice="coral",
        language="ko",
        turn_detection={"type": "semantic_vad", "eagerness": "low"},
        greeting=True,
    ),
)

# ══════════════════════════════════════════════════════════════════
# 헬퍼 함수
# ══════════════════════════════════════════════════════════════════

def _new_progress() -> dict:
    """통화 한 건 분량의 static_question 진행 상태.
    asked_ids: 이미 낸 문항 id, category_counts: 카테고리별로 몇 개 냈는지,
    pending_pair: pair_with로 묶여서 다음 차례에 무조건 내보내야 하는 문항 id."""
    return {
        "asked_ids": set(),
        "category_counts": {category: 0 for category in _QUESTIONS},
        "pending_pair": None,
    }


def _find_item(item_id: str) -> tuple[str, dict]:
    for category, data in _QUESTIONS.items():
        for item in data["items"]:
            if item["id"] == item_id:
                return category, item
    raise KeyError(item_id)


def _is_eligible(item: dict, category_items: list[dict], progress: dict) -> bool:
    """depends_on이 있는 문항(예: 낙상 후속 질문)은 그 트리거 문항을 아직 안 냈으면
    순서상 말이 안 되므로 후보에서 제외한다. 실제 답변이 조건과 맞는지는 우리 쪽에서
    알 방법이 없어서(모델만 실제 발화를 듣는다) 그 판단은 프롬프트로 모델에 위임한다.

    pair_with로 묶인 문항(예: sis_encode/sis_recall)은 둘 다 아직 안 냈다면 JSON에서
    먼저 나오는 쪽만 먼저 선택 가능하게 한다 — 단어를 알려주기도 전에 회상 질문부터
    나가는 등 순서가 뒤집히는 걸 막기 위해서다."""
    depends = item.get("depends_on")
    if depends and depends["item"] not in progress["asked_ids"]:
        return False

    pair = item.get("pair_with")
    if pair and item["id"] not in progress["asked_ids"] and pair not in progress["asked_ids"]:
        ids_in_order = [it["id"] for it in category_items]
        if ids_in_order.index(item["id"]) > ids_in_order.index(pair):
            return False
    return True


def _question_payload(category: str, item: dict) -> dict:
    instructions = _QUESTIONS[category]["instructions"]
    return {
        "done": False,
        "category": category,
        "item_id": item["id"],
        "type": item.get("type", "question"),
        "question": item["question"],
        "depends_on": item.get("depends_on"),
        "negative_signal": instructions["negative_signal"],
        "on_negative": instructions["on_negative"],
    }


def _pick_next_question(progress: dict, category_hint: str = "") -> dict | None:
    """다음에 물어볼 문항을 정하고 progress를 갱신한다. 더 물어볼 게 없으면 None."""
    pending = progress["pending_pair"]
    if pending and pending not in progress["asked_ids"]:
        category, item = _find_item(pending)
        progress["asked_ids"].add(item["id"])
        progress["category_counts"][category] += 1
        progress["pending_pair"] = None
        return _question_payload(category, item)

    if category_hint and category_hint in _QUESTIONS:
        candidates = [category_hint]
    else:
        candidates = [
            category for category, data in _QUESTIONS.items()
            if progress["category_counts"][category] < data["instructions"]["min_questions"]
        ]

    if not candidates:
        return None

    category = random.choice(candidates)
    category_items = _QUESTIONS[category]["items"]
    unasked = [
        item for item in category_items
        if item["id"] not in progress["asked_ids"] and _is_eligible(item, category_items, progress)
    ]
    if not unasked:
        return None

    item = random.choice(unasked)
    progress["asked_ids"].add(item["id"])
    progress["category_counts"][category] += 1
    if item.get("pair_with") and item["pair_with"] not in progress["asked_ids"]:
        progress["pending_pair"] = item["pair_with"]

    return _question_payload(category, item)


# (질문, 답변, 순번, 질문 시각) 엔트리로 재구성
def _convert_segments(segments: list[dict], started_at: datetime) -> list[dict]:

    entries: list[dict] = []
    seq = 1
    i = 0

    while i < len(segments):
        seg = segments[i]
        if seg.get("speaker") != "AGENT":
            i += 1
            continue

        question = seg["text"]
        asked_at = started_at + timedelta(seconds=seq * 10)

        answer_parts: list[str] = []
        j = i + 1
        while j < len(segments) and segments[j].get("speaker") != "AGENT":
            if segments[j].get("speaker") == "CUSTOMER":
                answer_parts.append(segments[j]["text"])
            j += 1

        answer = " ".join(answer_parts) if answer_parts else "(무응답)"
        entries.append({
            "sequence": seq,
            "question": question,
            "answer": answer,
            "asked_at": asked_at.isoformat(),
        })
        seq += 1
        i = j

    return entries

# ══════════════════════════════════════════════════════════════════
# Tool (clawops가 실시간 세션 중 모델 요청으로 호출)
# ══════════════════════════════════════════════════════════════════

@agent.tool
async def static_question(category: str = "") -> str:
    """Retrieve the next required wellbeing/health question to ask the
    resident this call, tracking which categories and items have already
    been covered so nothing is skipped or repeated.

    Call this with no arguments to get the next question in the normal
    flow. If the resident's last answer matched that question's
    negative_signal, call this again with category set to that same
    category to stay there and get a deeper follow-up question instead of
    moving on. When the response has "done": true, every required category
    has been covered for this call.

    Args:
        category: Category key (e.g. "depression_gds5") to keep drilling
            into after a negative signal. Leave empty for normal progression.
    """
    info = _call_registry.get(_active_call_id or "")
    if info is None:
        return json.dumps({"done": True})

    progress = info.setdefault("progress", _new_progress())
    result = _pick_next_question(progress, category)
    if result is None:
        return json.dumps({"done": True})
    return json.dumps(result, ensure_ascii=False)

# ══════════════════════════════════════════════════════════════════
# 이벤트 핸들러 (clawops가 통화 진행 중 호출)
# ══════════════════════════════════════════════════════════════════

@agent.on("call_start")
async def on_start(call):
    global _active_call_id
    print(f"[AGENT] 통화 시작: {call.call_id}")
    _active_call_id = call.call_id
    if call.call_id in _call_registry:
        _call_registry[call.call_id]["started_at"] = datetime.now()
        _call_registry[call.call_id]["segments"] = []
        _call_registry[call.call_id]["progress"] = _new_progress()


@agent.on("transcript")
async def on_transcript(call, role, text):

    label = "어르신" if role == "user" else "AI"
    print(f"[AGENT] [{label}] {text}")
    if call.call_id not in _call_registry:
        return
    _call_registry[call.call_id]["segments"].append({
        "speaker": "CUSTOMER" if role == "user" else "AGENT",
        "text": text,
    })

# 통화 끝나면 main으로 내역 전송
@agent.on("call_end")
async def on_end(call):
    global _active_call_id
    print(f"[AGENT] 통화 종료: {call.call_id} ({call.duration:.1f}초)")
    if _active_call_id == call.call_id:
        _active_call_id = None

    if call.call_id not in _call_registry:
        return

    info = _call_registry.pop(call.call_id)
    recipient_id: int = info["recipient_id"]
    started_at: datetime = info.get("started_at", datetime.now())
    ended_at = datetime.now()
    segments: list[dict] = info.get("segments", [])

    entries = _convert_segments(segments, started_at)
    if not entries:
        print("[AGENT] 분석할 엔트리 없음")
        return

    ai_server_url = os.getenv("AI_SERVER_BASE_URL", "http://localhost:8000") + "/internal/report-call-end"
    payload = {
        "recipient_id": recipient_id,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "entries": entries,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                ai_server_url,
                json=payload,
                headers={"X-API-KEY": os.getenv("INTERNAL_API_KEY")},
            )
        print(f"[AGENT] 분석 요청 응답: {resp.status_code}")
    except Exception as e:
        print(f"[AGENT] 분석 요청 실패: {e}")

# ══════════════════════════════════════════════════════════════════
# main.py 연동 함수 (main에서 호출)
# ══════════════════════════════════════════════════════════════════

# AI 서버 활성화된 후 바로 에이전트 연결
async def connect_agent() -> None:

    print("[AGENT] claw-ops 연결 중...")
    await agent.connect()
    print("[AGENT] claw-ops 연결 완료")

# ClawOpsAgent로 전화 발신
async def initiate_call(recipient_id: int, phone_number: str, guardian_phone_number: str = "") -> None:

    _call_registry[phone_number] = {
        "recipient_id": recipient_id,
        "guardian_phone": guardian_phone_number,
        "started_at": datetime.now(),
        "segments": [],
        "progress": _new_progress(),
    }
    call = await agent.call(to=phone_number)
    info = _call_registry.pop(phone_number, None)
    if info:
        _call_registry[call.call_id] = info
    await call.wait()
