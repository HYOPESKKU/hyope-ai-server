import os
import random
import asyncio

import httpx
from datetime import datetime

from schemas import AnalyzeCallRequest
from llm_analyzer import analyze_with_llm
from main import resolve_risk_level, fix_adherence_records
from dummy_entries import DUMMY_ENTRY_SETS


async def _run_dummy_call(recipient_id: int) -> None:
    """더미 entries로 LLM 분석 후 Spring /internal/call-results로 결과를 전송한다."""
    entries = random.choice(DUMMY_ENTRY_SETS)
    started_at = datetime.now()

    analyze_request = AnalyzeCallRequest(callLogId=0, recipientId=recipient_id, entries=entries)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, analyze_with_llm, analyze_request)

    assessment = result["assessment"]
    assessment["riskLevel"] = resolve_risk_level(assessment["overallRisk"])
    adherence_records = fix_adherence_records(result.get("adherenceRecords", []))

    ended_at = datetime.now()
    payload = {
        "recipient_id": recipient_id,
        "call_log": {
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "status": "COMPLETED",
        },
        "call_log_entries": [
            {"sequence": e.sequence, "question": e.question, "answer": e.answer, "asked_at": started_at.isoformat()}
            for e in entries
        ],
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
            {"adherence_type": r["adherenceType"], "slot": r["slot"], "is_needed": r["isNeeded"], "is_done": r["isDone"]}
            for r in adherence_records
        ],
    }

    spring_url = os.getenv("SPRING_BASE_URL", "http://localhost:8080") + "/internal/call-results"
    internal_api_key = os.getenv("INTERNAL_API_KEY")
    print(f"[SPRING] 전송 시작: {spring_url}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            spring_url,
            json=payload,
            headers={"X-API-KEY": internal_api_key}
        )
        print(f"[SPRING] 응답 코드: {resp.status_code}")
