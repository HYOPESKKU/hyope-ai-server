from fastapi import FastAPI, HTTPException
from datetime import datetime

from schemas import AnalyzeCallRequest, AnalyzeCallResponse
from llm_analyzer import analyze_with_llm


app = FastAPI(title="HYOPE AI Analysis Server")


@app.get("/")
def health_check():
    return {
        "status": "ok",
        "message": "HYOPE AI server is running"
    }


def fix_adherence_records(records):
    fixed = []

    for record in records:
        adherence_type = record.get("adherenceType")
        if adherence_type not in ["MEDICATION", "MEAL"]:
            continue

        slot = record.get("slot")
        if slot not in ["BREAKFAST", "LUNCH", "DINNER"]:
            slot = "BREAKFAST"

        fixed.append({
            "adherenceType": adherence_type,
            "slot": slot,
            "isNeeded": bool(record.get("isNeeded", True)),
            "isDone": bool(record.get("isDone", False))
        })

    return fixed


@app.post("/analyze-call", response_model=AnalyzeCallResponse)
async def analyze_call(request: AnalyzeCallRequest):
    try:
        result = analyze_with_llm(request)

        assessment = result["assessment"]
        adherence_records = fix_adherence_records(result.get("adherenceRecords", []))

        return {
            "callLogId": request.callLogId,
            "recipientId": request.recipientId,
            "measuredAt": datetime.now(),
            "assessment": assessment,
            "adherenceRecords": adherence_records
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))