import os
import random
import asyncio
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Response
from datetime import datetime

from schemas import AnalyzeCallRequest, AnalyzeCallResponse, CallRequest
from llm_analyzer import analyze_with_llm
from phq_classifier import classify_phq_item, identify_phq_item
from claw_handler import initiate_call, agent, connect_agent
from dummy_entries import DUMMY_ENTRY_SETS
from voiceml import router as voiceml_router


# 서버 시작 시점에 필수 환경변수 확인 — 요청이 들어온 뒤에 터지지 않도록
if not os.getenv("GEMINI_API_KEY"):
    raise RuntimeError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.")


app = FastAPI(title="HYOPE AI Analysis Server")
app.include_router(voiceml_router)


@app.on_event("startup")
async def startup():
    await connect_agent()


def resolve_risk_level(overall_risk: float) -> str:
    """overallRisk 점수를 기준으로 riskLevel을 서버에서 재계산한다.
    LLM이 점수와 등급을 불일치하게 반환하는 경우를 방지하기 위해 항상 이 함수 결과를 사용한다."""
    if overall_risk <= 2:
        return "OBSERVE"
    elif overall_risk <= 5:
        return "CAUTION"
    elif overall_risk <= 7:
        return "WARNING"
    else:
        return "HIGH"


def fix_adherence_records(records: list) -> list:
    """LLM이 반환한 adherenceRecords를 검증하고 유효한 항목만 반환한다.
    타입이나 슬롯이 유효하지 않은 항목은 기본값으로 대체하지 않고 제외한다."""
    VALID_TYPES = {"MEDICATION", "MEAL"}
    VALID_SLOTS = {"BREAKFAST", "LUNCH", "DINNER"}

    fixed = []
    for record in records:
        adherence_type = record.get("adherenceType")
        if adherence_type not in VALID_TYPES:
            continue

        slot = record.get("slot")
        if slot not in VALID_SLOTS:
            # 잘못된 슬롯은 BREAKFAST로 대체하지 않고 제외 — 오염된 데이터 방지
            continue

        fixed.append({
            "adherenceType": adherence_type,
            "slot": slot,
            "isNeeded": bool(record.get("isNeeded", True)),
            "isDone": bool(record.get("isDone", False))
        })

    return fixed

async def _run_dummy_call(recipient_id: int) -> None:
    """더미 entries로 LLM 분석 후 Spring /call-result로 결과를 전송한다."""
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

    spring_url = os.getenv("SPRING_BASE_URL", "http://localhost:8080") + "/call-result"
    print(f"[SPRING] 전송 시작: {spring_url}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(spring_url, json=payload)
        print(f"[SPRING] 응답 코드: {resp.status_code}")


@app.post("/call")
async def request_call(request: CallRequest, background_tasks: BackgroundTasks):
    """Spring CallScheduler로부터 통화 요청을 받아 더미 entries로 LLM 분석을 실행한다.
    즉시 200을 반환하고 분석은 백그라운드에서 처리한다."""
    background_tasks.add_task(initiate_call, request.recipientId, request.phoneNumber)
    #background_tasks.add_task(_run_dummy_call, request.recipientId)
    return {"status": "accepted"}


# webhook은 agent 모드에서 agent.router가 처리하므로 비활성화
# @app.post("/webhook/transcript")
# async def transcript_webhook(req: Request, background_tasks: BackgroundTasks):
#     form = await req.form()
#     call_status = form.get("CallStatus")
#     call_id = form.get("CallId")
#     if call_status == "completed" and call_id:
#         transcript_url = form.get("TranscriptUrl")
#         background_tasks.add_task(handle_transcript, call_id, transcript_url)
#     return Response(status_code=200)


@app.get("/")
def health_check():
    return {
        "status": "ok",
        "message": "HYOPE AI server is running"
    }


@app.post("/analyze-call", response_model=AnalyzeCallResponse)
async def analyze_call(request: AnalyzeCallRequest):
    # 어르신이 아무 답변도 하지 않은 경우(무응답·통화 실패) LLM 호출 없이 조기 종료
    valid_entries = [e for e in request.entries if e.answer.strip()]
    if not valid_entries:
        raise HTTPException(
            status_code=400,
            detail="분석 가능한 답변이 없습니다. 통화 중 어르신의 응답이 확인되지 않았습니다."
        )

    try:
        result = analyze_with_llm(request)

        assessment = result["assessment"]

        # overallRisk 기준으로 riskLevel을 서버에서 재계산 (LLM 불일치 방지)
        assessment["riskLevel"] = resolve_risk_level(assessment["overallRisk"])

        adherence_records = fix_adherence_records(result.get("adherenceRecords", []))

        # PHQ-9 발화 분류 (병렬)
        loop = asyncio.get_event_loop()
        phq_pairs = [
            (identify_phq_item(e.question), e.answer)
            for e in request.entries
            if identify_phq_item(e.question) is not None
        ]
        if phq_pairs:
            phq_raw = await asyncio.gather(
                *[loop.run_in_executor(None, classify_phq_item, item_no, answer)
                  for item_no, answer in phq_pairs],
                return_exceptions=True,
            )
            phq_results = [r for r in phq_raw if isinstance(r, dict)]
        else:
            phq_results = []

        if any(r.get("risk_flag") for r in phq_results) and assessment["riskLevel"] in ("OBSERVE", "CAUTION"):
            assessment["riskLevel"] = "WARNING"

        return {
            "callLogId": request.callLogId,
            "recipientId": request.recipientId,
            "measuredAt": datetime.now(),
            "assessment": assessment,
            "adherenceRecords": adherence_records,
            "phqResults": phq_results,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
