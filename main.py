import os
import asyncio
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header, Depends

from data.schemas import AnalyzeCallRequest, CallRequest, CallLogEntry, CallResultsRequest
from eval.llm_analyzer import analyze_with_llm
from eval.phq_classifier import classify_phq_item, identify_phq_item
from voice_agent import initiate_call, agent, connect_agent

app = FastAPI(title="HYOPE AI Analysis Server")

# 지표 분석 API KEY
if not os.getenv("GEMINI_API_KEY"):
    raise RuntimeError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.")

# 내부 서버 검증 API KEY
if not os.getenv("INTERNAL_API_KEY"):
    raise RuntimeError("INTERNAL_API_KEY 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.")

# ══════════════════════════════════════════════════════════════════
# 헬퍼 함수
# ══════════════════════════════════════════════════════════════════

# 내부 서버 검증
def verify_internal_api_key(x_api_key: str = Header(None)):

    expected_key = os.getenv("INTERNAL_API_KEY")
    if not expected_key or x_api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

# 종합 위험도 수치 정규화
def resolve_risk_level(overall_risk: float) -> str:

    if overall_risk <= 2:
        return "OBSERVE"
    elif overall_risk <= 5:
        return "CAUTION"
    elif overall_risk <= 7:
        return "WARNING"
    else:
        return "HIGH"

# 타입/슬롯 데이터 valid 검증
def fix_adherence_records(records: list) -> list:

    VALID_TYPES = {"MEDICATION", "MEAL"}
    VALID_SLOTS = {"BREAKFAST", "LUNCH", "DINNER"}

    fixed = []
    for record in records:
        adherence_type = record.get("adherenceType")
        if adherence_type not in VALID_TYPES:
            continue

        slot = record.get("slot")
        if slot not in VALID_SLOTS:
            continue

        fixed.append({
            "adherenceType": adherence_type,
            "slot": slot,
            "isNeeded": bool(record.get("isNeeded", True)),
            "isDone": bool(record.get("isDone", False))
        })

    return fixed

# phq 해당하는 질문/답변 분류
async def classify_phq(llm_entries: list[CallLogEntry]) -> list[dict]:

    loop = asyncio.get_event_loop()
    phq_pairs = [
        (identify_phq_item(e.question), e.answer)
        for e in llm_entries
        if identify_phq_item(e.question) is not None
    ]
    if not phq_pairs:
        return []

    print(f"[MAIN] PHQ-9 분류 시작 ({len(phq_pairs)}문항)")
    phq_raw = await asyncio.gather(
        *[loop.run_in_executor(None, classify_phq_item, item_no, answer) for item_no, answer in phq_pairs],
        return_exceptions=True,
    )
    phq_results = [r for r in phq_raw if isinstance(r, dict)]
    print(f"[MAIN] PHQ-9 분류 완료: {[(r.get('item_no'), r.get('score')) for r in phq_results]}")
    return phq_results

# 전체 통화 분석 & 종합 위험도 정규화 & 타입/슬롯 데이터 valid 검증
async def analyze_call_content(
    recipient_id: int, llm_entries: list[CallLogEntry], phq_results: list[dict]
) -> tuple[dict, list]:
    
    loop = asyncio.get_event_loop()

    print("[MAIN] LLM 분석 시작")
    result = await loop.run_in_executor(
        None,
        analyze_with_llm,
        AnalyzeCallRequest(callLogId=0, recipientId=recipient_id, entries=llm_entries),
    )
    print("[MAIN] LLM 분석 완료")

    assessment = result["assessment"]
    assessment["riskLevel"] = resolve_risk_level(assessment["overallRisk"])

    has_suicide_risk = any(r.get("risk_flag") for r in phq_results)
    if has_suicide_risk and assessment["riskLevel"] in ("OBSERVE", "CAUTION"):
        assessment["riskLevel"] = "WARNING"

    adherence_records = fix_adherence_records(result.get("adherenceRecords", []))
    return assessment, adherence_records

# ══════════════════════════════════════════════════════════════════
# API 엔드포인트
# ══════════════════════════════════════════════════════════════════

@app.get("/")
def health_check():

    return {
        "status": "ok",
        "message": "HYOPE AI server is running"
    }

# 서버 활성화 된 후 한 번만 실행 - 통신 에이전트 연결
@app.on_event("startup")
async def startup():

    await connect_agent()

# 통화 요청 수신
@app.post("/call", dependencies=[Depends(verify_internal_api_key)])
async def request_call(request: CallRequest, background_tasks: BackgroundTasks):

    # 통화 세션 background task로 실행, 즉시 200 반환
    background_tasks.add_task(initiate_call, request.recipientId, request.phoneNumber, request.guardianPhoneNumber)
    return {"status": "accepted"}

# voice 에이전트 통화 종료 신호 수신 -> LLM 분석 후 메인 서버로 결과 전송
@app.post("/internal/report-call-end", dependencies=[Depends(verify_internal_api_key)])
async def report_call_results_endpoint(request: CallResultsRequest):

    llm_entries = [
        CallLogEntry(sequence=e.sequence, question=e.question, answer=e.answer)
        for e in request.entries
    ]
    call_log_entries = [
        {
            "sequence": e.sequence,
            "question": e.question,
            "answer": e.answer,
            "asked_at": e.asked_at.isoformat(),
        }
        for e in request.entries
    ]

    phq_results = await classify_phq(llm_entries)
    assessment, adherence_records = await analyze_call_content(request.recipient_id, llm_entries, phq_results)

    payload = {
        "recipient_id": request.recipient_id,
        "call_log": {
            "started_at": request.started_at.isoformat(),
            "ended_at": request.ended_at.isoformat(),
            "status": "COMPLETED",
        },
        "call_log_entries": call_log_entries,
        "assessment": {
            "measured_at": request.ended_at.isoformat(),
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

    spring_url = os.getenv("SPRING_BASE_URL", "http://localhost:8080") + "/internal/call-results"
    internal_api_key = os.getenv("INTERNAL_API_KEY")
    print(f"[MAIN] Spring 전송: {spring_url}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            spring_url,
            json=payload,
            headers={"X-API-KEY": internal_api_key}
        )
    print(f"[MAIN] Spring 응답: {resp.status_code}")

    return {"status": "ok"}
