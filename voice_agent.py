import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from clawops.agent import ClawOpsAgent

from realtime_session import RealtimeSession

# ══════════════════════════════════════════════════════════════════
# 설정 (질문 데이터, 시스템 프롬프트)
# ══════════════════════════════════════════════════════════════════

_QUESTIONS: dict = json.loads((Path(__file__).parent / "data" / "questions.json").read_text(encoding="utf-8"))

SYSTEM_PROMPT = (
    "Speak only Korean (한국어), in short sentences. "
    "Start the call with a brief, warm greeting to the resident before "
    "anything else — the resident hasn't said anything yet, so greet them "
    "first. As part of that same greeting, briefly mention that you'll be "
    "asking a few questions to check on their health, so the questions "
    "that follow don't feel sudden. "
    "Every question you ask must come from calling the static_question "
    "tool. Call it right after your greeting, and call it again after "
    "every answer. Never invent a question yourself. Never say anything "
    "about calling a tool, never say the word static_question out loud, "
    "never narrate what you are doing — just speak the question it "
    "returns, and nothing else, in the same turn you call it. Always "
    "speak the exact question a call returns before doing anything else "
    "— never call static_question a second time without first speaking "
    "what the previous call returned. "
    "After the resident answers a question, react with one brief, warm "
    "sentence acknowledging what they said before calling static_question "
    "again for the next one — don't jump straight to the next question "
    "with no reaction. "
    "If the resident asks you to explain or clarify the current question "
    "instead of answering it (e.g. asking what something in it means), "
    "explain it yourself in plain terms using only the information already "
    "in that question's text — don't call static_question again and don't "
    "just repeat the question verbatim; wait for their actual answer "
    "before moving on. "
    "If the resident's answer matches that question's negative_signal, "
    "call static_question again with category set to that same category "
    "AND was_negative set to true. Otherwise call it again with no "
    "arguments. "
    "Some questions include depends_on — only ask it if the named earlier "
    "answer matched; otherwise skip it silently and call static_question "
    "again for a different question. "
    "Keep calling static_question until it returns done: true. At that "
    "point, ask the resident if there's anything else they'd like to say "
    "or ask. If they have nothing more to add, say a warm goodbye and "
    "then call the end_checkin tool to actually end the call — call it "
    "only right after your goodbye, never before, and never say anything "
    "about calling it."
)

_WEEKDAYS_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

# ══════════════════════════════════════════════════════════════════
# 모듈 상태 + 에이전트 초기화
# ══════════════════════════════════════════════════════════════════

# call_id → {recipient_id, guardian_phone, started_at, segments, progress}
_call_registry: dict[str, dict] = {}

# ClawOpsAgent가 session 인스턴스를 콜 간에 공유해서 한 번에 통화 하나만 처리한다.
# tool 함수는 clawops로부터 call 객체를 인자로 받지 못하므로, "지금 어떤 통화인지"를
# 여기 전역 변수로 추적해야 static_question이 해당 통화의 진행 상태를 찾을 수 있다.
_active_call_id: str | None = None
_active_call: Any | None = None  # end_checkin이 call.hangup()을 부르려면 call 객체 자체가 필요하다

# clawops가 기본 제공하는 OpenAIRealtime 세션 대신, 직접 짠 RealtimeSession을 쓴다.
# builtin tool(hang_up/collect_dtmf/send_dtmf/transfer_call)은 아예 구현하지 않았으므로
# 모델이 그중 하나를 오작동으로 호출할 일 자체가 없고, 응답 생성도 턴당 정확히 한 번만
# 나가도록(realtime_session.py의 _maybe_create_response) 직접 통제한다.
_session = RealtimeSession(
    system_prompt=SYSTEM_PROMPT,
    voice="coral",
    language="ko",
)

# clawops 자체의 자동 prewarm(벨 울리는 동안 백그라운드로 LLM WS를 미리 여는 것)을
# 끈다 — clawops는 이걸 10초 타임아웃으로 감싸는데, 콜드 커넥션에서 그 안에 못 끝나면
# 강제로 취소하고 나중에 처음부터 다시 시작한다. 이때 취소된 첫 시도의 연결이 안 닫힌
# 채 남아서 두 번째 시도와 동시에 같은 통화를 놓고 경쟁하는 문제가 실제로 있었다.
# 대신 initiate_call()에서 발신 전에 session.prewarm()을 직접, 타임아웃 없이 완전히
# 끝내놓고 나서 전화를 건다 — 세션에 tool/instructions가 확실히 반영된 뒤에만 통화가
# 시작된다.
agent = ClawOpsAgent(
    from_=os.getenv("CLAWOPS_FROM_NUMBER", ""),
    session=_session,
    prewarm_enabled=False,
)

# ══════════════════════════════════════════════════════════════════
# 헬퍼 함수
# ══════════════════════════════════════════════════════════════════

_RECALL_DELAY_ITEMS = 2  # pair_with 문항(예: sis_recall)을 내보내기 전 최소로 지나야 하는 다른 문항 수


def _reference_answer(item_id: str) -> str | None:
    """지남력 문항(오늘 요일/월/연도)의 실제 정답을 계산해서 돌려준다. 모델은 오늘
    날짜를 알 방법이 없어서, 이 값을 안 주면 negative_signal 판단을 위해 스스로
    추측하다 틀린 값을 말해버리는(hallucination) 문제가 생긴다. 어르신께는 절대
    말하면 안 되고 판단에만 쓰라고 프롬프트에서 명시한다."""
    now = datetime.now()
    if item_id == "sis_day":
        return _WEEKDAYS_KO[now.weekday()]
    if item_id == "sis_month":
        return f"{now.month}월"
    if item_id == "sis_year":
        return f"{now.year}년"
    return None


def _time_of_day_phrase() -> str:
    """시간대 기반 context_bridge_examples를 쓰는 카테고리(예: daily_check)를 위한
    현재 시간대 문구. '그런데 하나 더 여쭤보고 싶은데요' 식의 뻔한 전환 대신, 지금이
    저녁인지 아침인지에 맞춰 물어보라고 모델에게 참고 정보로 준다."""
    hour = datetime.now().hour
    if hour < 11:
        return "아침"
    if hour < 17:
        return "점심"
    if hour < 21:
        return "저녁"
    return "밤"


# 환자 히스토리상 특정 우려가 있을 때만 물어봐야 하는 카테고리 → 그 근거가 되는
# history_flags 키. 아직 히스토리를 실제로 조회해오는 로직이 없어서(추후 구현 예정)
# history_flags는 항상 비어있는 채로 넘어오고, 그 결과 이 카테고리들은 지금은 기본
# 질문 세션에 전혀 포함되지 않는다. 나중에 히스토리 조회가 붙으면 initiate_call()에
# 실제 history_flags만 넘기면 되고, 이 아래 로직은 이미 그 값을 받아 쓸 준비가 돼있다.
_CONDITIONAL_CATEGORIES: dict[str, str] = {
    "cognitive_sis": "cognitive_concern",
}


def _active_categories(history_flags: dict | None) -> set[str]:
    """이번 통화에서 실제로 다룰 카테고리 집합. _CONDITIONAL_CATEGORIES에 없는
    카테고리는 항상 포함되고, 있는 카테고리는 history_flags에 해당 신호가
    True일 때만 포함된다."""
    history_flags = history_flags or {}
    return {
        category for category in _QUESTIONS
        if category not in _CONDITIONAL_CATEGORIES
        or history_flags.get(_CONDITIONAL_CATEGORIES[category])
    }


def _new_progress(history_flags: dict | None = None) -> dict:
    """통화 한 건 분량의 static_question 진행 상태.
    asked_ids: 이미 낸 문항 id, category_counts: 카테고리별로 몇 개 냈는지,
    delayed_pair: pair_with로 묶인 문항(예: sis_recall)을 몇 번째 문항 이후에
    내보내야 하는지({"item_id", "ready_at"}) — 단어를 알려주자마자 바로 회상을
    묻지 않고, 대화가 좀 지난 뒤에 물어보게 하려고 강제로 즉시 이어붙이지 않는다.
    current_category: 직전에 낸 문항의 카테고리(전환 로그 출력용).
    active_categories: 이번 통화에서 실제로 다룰 카테고리 집합(조건부 카테고리는
    history_flags에 신호가 없으면 빠진다)."""
    return {
        "asked_ids": set(),
        "category_counts": {category: 0 for category in _QUESTIONS},
        "delayed_pair": None,
        "current_category": None,
        "active_categories": _active_categories(history_flags),
    }


def _log_category_change(progress: dict, category: str) -> None:
    previous = progress["current_category"]
    if previous != category:
        print(f"[AGENT] 카테고리 전환: {previous or '(시작)'} → {category}")
        progress["current_category"] = category


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
    나가는 등 순서가 뒤집히는 걸 막기 위해서다. pair의 뒤쪽 문항(예: sis_recall)은
    일반 무작위 후보에서 아예 제외한다 — _pick_next_question의 delayed_pair 게이트를
    통해서만 나가게 해서, 앞쪽 문항 직후에 바로 이어지지 않고 지연 시간을 둘 수 있다."""
    depends = item.get("depends_on")
    if depends and depends["item"] not in progress["asked_ids"]:
        return False

    pair = item.get("pair_with")
    if pair and item["id"] not in progress["asked_ids"] and pair not in progress["asked_ids"]:
        ids_in_order = [it["id"] for it in category_items]
        if ids_in_order.index(item["id"]) > ids_in_order.index(pair):
            return False

    delayed = progress.get("delayed_pair")
    if delayed and delayed["item_id"] == item["id"]:
        return False
    return True


def _question_payload(category: str, item: dict) -> dict:
    instructions = _QUESTIONS[category]["instructions"]
    payload = {
        "done": False,
        "category": category,
        "item_id": item["id"],
        "type": item.get("type", "question"),
        "question": item["question"],
        "depends_on": item.get("depends_on"),
        "negative_signal": instructions["negative_signal"],
        "on_negative": instructions["on_negative"],
    }
    reference = _reference_answer(item["id"])
    if reference is not None:
        payload["reference_answer_do_not_say_aloud"] = reference
    bridge_examples = instructions.get("context_bridge_examples")
    if bridge_examples:
        phrase = _time_of_day_phrase()
        example = bridge_examples.get(phrase)
        if example:
            payload["context_hint"] = (
                f"지금은 {phrase} 시간대입니다. 자연스럽게 지금 시간대를 언급하며 "
                f"이어서 물어보세요. 예: '{example}' 같은 식으로."
            )
    notes = [n for n in (instructions.get("note"), item.get("note")) if n]
    if notes:
        payload["note"] = " ".join(notes)
    return payload


def _force_serve_delayed(progress: dict) -> dict | None:
    """delayed_pair로 지연 중인 문항(예: sis_recall)을 ready_at 여부와 상관없이
    지금 바로 내보낸다. 다른 후보가 바닥나서 통화가 조기 종료될 위기일 때 마지막
    안전장치로 쓴다 — 늦게라도 물어보는 게 아예 안 물어보는 것보다 낫다."""
    delayed = progress["delayed_pair"]
    if not delayed or delayed["item_id"] in progress["asked_ids"]:
        return None
    category, item = _find_item(delayed["item_id"])
    progress["asked_ids"].add(item["id"])
    progress["category_counts"][category] += 1
    progress["delayed_pair"] = None
    _log_category_change(progress, category)
    return _question_payload(category, item)


def _pick_next_question(
    progress: dict, category_hint: str = "", was_negative: bool = False
) -> dict | None:
    """다음에 물어볼 문항을 정하고 progress를 갱신한다. 더 물어볼 게 없으면 None.

    category_hint는 was_negative가 True일 때만 반영한다 — 모델이 "부정적 신호였다"고
    명시하지 않았는데도 습관적으로 같은 category를 계속 넘기는 경우(실제로 관찰된
    문제)를 프롬프트 신뢰만으로 막을 수 없어서, 코드에서 이 조건을 강제한다."""
    delayed = progress["delayed_pair"]
    if delayed and delayed["item_id"] not in progress["asked_ids"] and len(progress["asked_ids"]) >= delayed["ready_at"]:
        category, item = _find_item(delayed["item_id"])
        progress["asked_ids"].add(item["id"])
        progress["category_counts"][category] += 1
        progress["delayed_pair"] = None
        _log_category_change(progress, category)
        return _question_payload(category, item)

    effective_hint = (
        category_hint if (was_negative and category_hint in progress["active_categories"]) else ""
    )

    if effective_hint:
        candidates = [effective_hint]
    else:
        candidates = [
            category for category, data in _QUESTIONS.items()
            if category in progress["active_categories"]
            and progress["category_counts"][category] < data["instructions"]["min_questions"]
        ]

    if not candidates:
        # 다른 건 다 채워졌는데 아직 지연 중인 pair가 남아있으면(지연 시간이 덜 지났어도)
        # 놓치지 않고 지금 내보낸다 — 늦게라도 물어보는 게 아예 안 물어보는 것보다 낫다.
        forced = _force_serve_delayed(progress)
        if forced:
            return forced
        return None

    category = random.choice(candidates)
    category_items = _QUESTIONS[category]["items"]
    unasked = [
        item for item in category_items
        if item["id"] not in progress["asked_ids"] and _is_eligible(item, category_items, progress)
    ]
    if not unasked:
        # 이 category 안에서 회상 문항만 지연 게이트에 막혀 더 낼 게 없어 보이는 경우도
        # 마찬가지로 봐준다 — 그냥 None을 반환해 통화가 조기 종료되지 않게 한다.
        forced = _force_serve_delayed(progress)
        if forced:
            return forced
        return None

    item = random.choice(unasked)
    progress["asked_ids"].add(item["id"])
    progress["category_counts"][category] += 1
    if item.get("pair_with") and item["pair_with"] not in progress["asked_ids"]:
        progress["delayed_pair"] = {
            "item_id": item["pair_with"],
            "ready_at": len(progress["asked_ids"]) + _RECALL_DELAY_ITEMS,
        }

    _log_category_change(progress, category)
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
async def static_question(category: str = "", was_negative: bool = False) -> str:
    """Retrieve the next required wellbeing/health question to ask the
    resident this call, tracking which categories and items have already
    been covered so nothing is skipped or repeated.

    Call this with no arguments to get the next question in the normal
    flow. If the resident's last answer matched that question's
    negative_signal, call this again with category set to that same
    category AND was_negative set to true, to stay there and get a deeper
    follow-up question instead of moving on. category is only honored when
    was_negative is true — passing it without was_negative has no effect,
    so don't bother. When the response has "done": true, every required
    category has been covered for this call.

    Args:
        category: Category key (e.g. "depression_gds5") to keep drilling
            into. Only takes effect when was_negative is true.
        was_negative: Whether the resident's last answer matched that
            question's negative_signal.
    """
    info = _call_registry.get(_active_call_id or "")
    if info is None:
        return json.dumps({"done": True})

    progress = info.setdefault("progress", _new_progress())
    result = _pick_next_question(progress, category, was_negative)
    if result is None:
        return json.dumps({"done": True})
    return json.dumps(result, ensure_ascii=False)


@agent.tool
async def end_checkin() -> str:
    """End the phone call now. Only call this after static_question has
    returned done: true AND the resident has confirmed (or been given a
    clear chance to say) they have nothing else to add, and you have
    already said goodbye. This hangs up immediately — say your goodbye
    first, then call this.
    """
    if _active_call is not None:
        await _active_call.hangup()
    return "call ending"

# clawops는 원래 전화가 실제로 응답된 뒤(_start_call_session)에야 session에
# tool registry를 넘겨준다(MCP tool을 통화별로 격리하려고 콜마다 fork해서 준다).
# 우리는 MCP를 안 쓰고 tool이 static_question 하나뿐이라 격리가 필요 없으므로,
# clawops의 그 뒤늦은 전달을 기다리지 않고 agent에 등록된 레지스트리를 여기서
# 바로 세션에 심어둔다 — 그래야 initiate_call()이 발신 전에 미리 하는 prewarm()
# 시점에도 tool_schemas가 비어있지 않고 static_question이 실제로 들어간다.
_session.set_tool_registry(agent._tool_registry)

# ══════════════════════════════════════════════════════════════════
# 이벤트 핸들러 (clawops가 통화 진행 중 호출)
# ══════════════════════════════════════════════════════════════════

@agent.on("call_start")
async def on_start(call):
    """initiate_call()이 이미 발신 전에 _active_call_id/registry를 설정해두므로
    여기선 안전망 성격(예: 나중에 인바운드 통화 등 다른 경로가 생기는 경우)이다."""
    global _active_call_id, _active_call
    print(f"[AGENT] 통화 시작: {call.call_id}")
    _active_call_id = call.call_id
    _active_call = call
    _call_registry.setdefault(call.call_id, {
        "started_at": datetime.now(),
        "segments": [],
        "progress": _new_progress(),
    })


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
    global _active_call_id, _active_call
    print(f"[AGENT] 통화 종료: {call.call_id} ({call.duration:.1f}초)")
    if _active_call_id == call.call_id:
        _active_call_id = None
        _active_call = None

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

    # 이 프로세스에서 OpenAI Realtime에 처음 붙는 연결만 유독 tool 호출을 아예 안 하고
    # 필러만 반복하다 포기하는 현상이 실통화에서 반복 관찰됐다 — 콜드 스타트 성격으로
    # 보인다. 실제 통화가 오기 전에 미리 한 번 연결했다 끊어서 그 "처음"을 여기서
    # 소모해둔다. prewarm()/stop() 모두 멱등적이라 이후 진짜 통화의 prewarm()에는
    # 영향 없다.
    print("[AGENT] Realtime 세션 워밍업 중...")
    try:
        await _session.prewarm()
        await _session.stop()
        print("[AGENT] Realtime 세션 워밍업 완료")
    except Exception as e:
        print(f"[AGENT] Realtime 세션 워밍업 실패(무시하고 진행): {e}")

# ClawOpsAgent로 전화 발신
async def initiate_call(
    recipient_id: int,
    phone_number: str,
    guardian_phone_number: str = "",
    history_flags: dict | None = None,
) -> None:
    """history_flags: 환자 히스토리 기반으로 조건부 카테고리(예: cognitive_sis)를
    활성화할지 정하는 신호({"cognitive_concern": True} 등). 히스토리를 실제로
    조회해오는 로직이 아직 없어서 호출하는 쪽(main.py)에서 지금은 이 인자를 안
    넘기고, 그러면 기본값 None → 조건부 카테고리는 전부 비활성 상태로 진행된다."""
    global _active_call_id, _active_call

    _call_registry[phone_number] = {
        "recipient_id": recipient_id,
        "guardian_phone": guardian_phone_number,
        "started_at": datetime.now(),
        "segments": [],
        "progress": _new_progress(history_flags),
    }
    # 전화를 걸기 전에 세션(tool/instructions 반영)을 완전히 끝내놓는다 — clawops의
    # 자동 prewarm(prewarm_enabled=False로 꺼둠)에 있던 10초 타임아웃 레이스 없이,
    # 여기선 시간 제한 없이 session.updated 확인까지 마친 뒤에야 발신한다.
    await _session.prewarm()
    call = await agent.call(to=phone_number)
    info = _call_registry.pop(phone_number, None)
    if info:
        _call_registry[call.call_id] = info
    _active_call_id = call.call_id
    _active_call = call
    await call.wait()
