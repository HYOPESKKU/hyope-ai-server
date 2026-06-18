import os
from datetime import datetime, timedelta

import httpx
from clawops import ClawOps

from llm_analyzer import analyze_with_llm
from schemas import AnalyzeCallRequest, CallLogEntry

# call_id → {recipient_id, started_at} 매핑 — 통화 완료 후 삭제
_call_registry: dict[str, dict] = {}

VALID_TYPES = {"MEDICATION", "MEAL"}
VALID_SLOTS = {"BREAKFAST", "LUNCH", "DINNER"}


def _get_client() -> ClawOps:
    return ClawOps(
        api_key=os.getenv("CLAWOPS_API_KEY"),
        account_id=os.getenv("CLAWOPS_ACCOUNT_ID"),
    )


def initiate_call(recipient_id: int, phone_number: str) -> None:
    """claw-ops로 전화를 발신하고 call_id를 레지스트리에 등록한다."""
    client = _get_client()
    call = client.calls.create(
        to=phone_number,
        from_=os.getenv("CLAWOPS_FROM_NUMBER"),
        url=os.getenv("CLAWOPS_VOICEML_URL"),  # TODO: VoiceML 질문 스크립트 URL 확정 필요
        status_callback=os.getenv("WEBHOOK_BASE_URL") + "/webhook/transcript",
    )
    _call_registry[call.call_id] = {
        "recipient_id": recipient_id,
        "started_at": datetime.now(),
    }


async def handle_transcript(call_id: str, transcript_url: str) -> None:
    """transcript.completed 웹훅 수신 후 LLM 분석 → Spring /call-result 전송."""
    if call_id not in _call_registry:
        return

    info = _call_registry.pop(call_id)
    recipient_id: int = info["recipient_id"]
    started_at: datetime = info["started_at"]
    ended_at = datetime.now()

    # GCS 서명 URL에서 transcript 다운로드
    async with httpx.AsyncClient() as client:
        resp = await client.get(transcript_url)
    segments: list[dict] = resp.json()

    llm_entries, spring_entries = _convert_segments(segments, started_at)
    if not llm_entries:
        return

    # LLM 분석
    result = analyze_with_llm(
        AnalyzeCallRequest(callLogId=0, recipientId=recipient_id, entries=llm_entries)
    )

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
    }

    spring_url = os.getenv("SPRING_BASE_URL", "http://localhost:8080") + "/call-result"
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(spring_url, json=payload)


def _convert_segments(
    segments: list[dict], started_at: datetime
) -> tuple[list[CallLogEntry], list[dict]]:
    """AGENT/CUSTOMER 세그먼트를 LLM 입력용 + Spring 저장용 엔트리로 변환.

    AGENT 발화를 question, 이후 연속된 CUSTOMER 발화를 answer로 묶어 쌍을 만든다.
    CUSTOMER 답변이 없는 AGENT 발화는 포함하지 않는다.
    """
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
        asked_at = started_at + timedelta(seconds=seg.get("start", 0))

        answer_parts: list[str] = []
        j = i + 1
        while j < len(segments) and segments[j].get("speaker") != "AGENT":
            if segments[j].get("speaker") == "CUSTOMER":
                answer_parts.append(segments[j]["text"])
            j += 1

        if answer_parts:
            answer = " ".join(answer_parts)
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
