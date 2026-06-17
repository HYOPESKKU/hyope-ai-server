import os
from fastapi import FastAPI, HTTPException
from datetime import datetime

from schemas import AnalyzeCallRequest, AnalyzeCallResponse
from llm_analyzer import analyze_with_llm


# 서버 시작 시점에 필수 환경변수 확인 — 요청이 들어온 뒤에 터지지 않도록
if not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.")


app = FastAPI(title="HYOPE AI Analysis Server")


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

        return {
            "callLogId": request.callLogId,
            "recipientId": request.recipientId,
            "measuredAt": datetime.now(),
            "assessment": assessment,
            "adherenceRecords": adherence_records
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
