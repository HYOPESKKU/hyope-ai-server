import os
from datetime import datetime, timedelta

import httpx
from clawops.agent import ClawOpsAgent, OpenAIRealtime

SYSTEM_PROMPT = """"""

_call_registry: dict[str, dict] = {} # 통화 도중 실시간으로 정보 저장

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
# 이벤트 핸들러 (clawops가 통화 진행 중 호출)
# ══════════════════════════════════════════════════════════════════

@agent.on("call_start")
async def on_start(call):

    print(f"[AGENT] 통화 시작: {call.call_id}")
    if call.call_id in _call_registry:
        _call_registry[call.call_id]["started_at"] = datetime.now()
        _call_registry[call.call_id]["segments"] = []


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

    print(f"[AGENT] 통화 종료: {call.call_id} ({call.duration:.1f}초)")

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
    }
    call = await agent.call(to=phone_number)
    info = _call_registry.pop(phone_number, None)
    if info:
        _call_registry[call.call_id] = info
    await call.wait()
