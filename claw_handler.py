import os
import asyncio
from datetime import datetime, timedelta

import httpx
from clawops.agent import ClawOpsAgent, OpenAIRealtime

from llm_analyzer import analyze_with_llm
from schemas import AnalyzeCallRequest, CallLogEntry

VALID_TYPES = {"MEDICATION", "MEAL"}
VALID_SLOTS = {"BREAKFAST", "LUNCH", "DINNER"}

SYSTEM_PROMPT = """당신은 어르신의 건강을 돌보는 친절한 AI 상담 전화 도우미입니다.

# 핵심 규칙
- 반드시 한 번에 질문 하나만 하세요. 어르신이 답하면 공감 한 마디 후 다음 질문으로 넘어가세요.
- 질문을 두 개 이상 한 문장에 묶지 마세요. (예: "약은 드셨나요? 식사는요?" 금지)
- 답변을 들은 후 "아, 그러셨군요." / "다행이네요." / "많이 힘드셨겠어요." 같은 짧은 공감을 먼저 하세요.

# 진행 순서 (반드시 이 순서대로, 하나씩)
1. 인사: "안녕하세요, 효프 건강 확인 전화입니다. 잘 지내고 계세요?"
2. 복약 확인: "오늘 약은 드셨나요?"
3. 식사 확인: "오늘 식사는 하셨나요?"
4. 기분 확인: "요즘 기분은 어떠세요?"
5. 수면 확인: "요즘 잠은 잘 주무세요?"
6. 피로감 확인: "몸이 많이 피곤하거나 기운이 없으신 건 아닌가요?"
7. 신체 불편 확인: "몸이 어디 아프거나 불편하신 데는 없으세요?"
8. 인지 확인: "오늘이 몇 월 며칠인지 혹시 기억하세요?"
9. 마무리: 아래 중 하나를 무작위로 선택해 따뜻하게 마무리하세요.
   - "오늘도 건강하게 잘 지내셨네요, 내일도 좋은 하루 되세요!"
   - "어르신 덕분에 저도 기분이 좋아졌어요, 항상 건강하세요!"
   - "오늘 통화해서 정말 반가웠어요, 맛있는 거 많이 드세요!"
   - "어르신 목소리 듣고 나니 저도 힘이 나요, 좋은 하루 되세요!"
   - "오늘 하루도 수고 많으셨어요, 푹 쉬시고 내일 또 통화해요!"

# 추가 규칙
- 어르신이 걱정되는 이야기를 하면 자연스럽게 한 번 더 여쭤봐도 됩니다. 단, 그 후엔 다음 순서로 넘어가세요.
- 말투는 항상 따뜻하고 천천히, 쉬운 단어로 말하세요.
- 절대 의학적 진단을 내리지 마세요."""

# call_id → {recipient_id, started_at, segments}
_call_registry: dict[str, dict] = {}

agent = ClawOpsAgent(
    from_=os.getenv("CLAWOPS_FROM_NUMBER", ""),
    session=OpenAIRealtime(
        system_prompt=SYSTEM_PROMPT,
        voice="coral",
        language="ko",
        turn_detection={"type": "semantic_vad", "eagerness": "medium"},
        greeting=True,
    ),
)


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

    llm_entries, spring_entries = _convert_segments(segments, started_at)
    if not llm_entries:
        print("[AGENT] 분석할 엔트리 없음")
        return

    print("[AGENT] LLM 분석 시작")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        analyze_with_llm,
        AnalyzeCallRequest(callLogId=0, recipientId=recipient_id, entries=llm_entries)
    )
    print("[AGENT] LLM 분석 완료")

    assessment = result["assessment"]
    risk = assessment["overallRisk"]
    if risk <= 2:
        assessment["riskLevel"] = "OBSERVE"
    elif risk <= 5:
        assessment["riskLevel"] = "CAUTION"
    elif risk <= 7:
        assessment["riskLevel"] = "WARNING"
    else:
        assessment["riskLevel"] = "HIGH"

    adherence_records = [
        r for r in result.get("adherenceRecords", [])
        if r.get("adherenceType") in VALID_TYPES and r.get("slot") in VALID_SLOTS
    ]

    payload = {
        "recipient_id": recipient_id,
        "call_log": {
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "status": "COMPLETED",
        },
        "call_log_entries": spring_entries,
        "assessment": {
            "measured_at": ended_at.isoformat(),
            "depression_score": assessment["depressionScore"],
            "emotional_stability": assessment["emotionalStability"],
            "health_risk": assessment["healthRisk"],
            "cognitive_score": assessment["cognitiveScore"],
            "overall_risk": assessment["overallRisk"],
            "risk_level": assessment["riskLevel"],
            "summary": assessment["summary"],
            "recommendation": assessment["recommendation"],
        },
        "adherence_records": [
            {
                "adherence_type": r["adherenceType"],
                "slot": r["slot"],
                "is_needed": r["isNeeded"],
                "is_done": r["isDone"],
            }
            for r in adherence_records
        ],
        "phq_results": [],
    }

    spring_url = os.getenv("SPRING_BASE_URL", "http://localhost:8080") + "/internal/call-results"
    internal_api_key = os.getenv("INTERNAL_API_KEY")
    print(f"[AGENT] Spring 전송: {spring_url}")
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            spring_url,
            json=payload,
            headers={"X-API-KEY": internal_api_key}
        )
    print(f"[AGENT] Spring 응답: {resp.status_code}")


async def connect_agent() -> None:
    """서버 시작 시 claw-ops에 agent를 연결한다."""
    print("[AGENT] claw-ops 연결 중...")
    await agent.connect()
    print("[AGENT] claw-ops 연결 완료")


async def initiate_call(recipient_id: int, phone_number: str) -> None:
    """ClawOpsAgent로 전화를 발신하고 call_id를 레지스트리에 등록한다."""
    _call_registry[phone_number] = {
        "recipient_id": recipient_id,
        "started_at": datetime.now(),
        "segments": [],
    }
    call = await agent.call(to=phone_number)
    info = _call_registry.pop(phone_number, None)
    if info:
        _call_registry[call.call_id] = info
    await call.wait()


def _convert_segments(
    segments: list[dict], started_at: datetime
) -> tuple[list[CallLogEntry], list[dict]]:
    llm_entries: list[CallLogEntry] = []
    spring_entries: list[dict] = []
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
        llm_entries.append(CallLogEntry(sequence=seq, question=question, answer=answer))
        spring_entries.append({
            "sequence": seq,
            "question": question,
            "answer": answer,
            "asked_at": asked_at.isoformat(),
        })
        seq += 1
        i = j

    return llm_entries, spring_entries
