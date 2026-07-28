from pydantic import BaseModel, field_validator
from typing import Any, List, Literal, Optional
from datetime import datetime


class CallLogEntry(BaseModel):
    sequence: int
    question: str
    answer: str


class AnalyzeCallRequest(BaseModel):
    callLogId: int
    recipientId: int
    entries: List[CallLogEntry]


class AssessmentResult(BaseModel):
    depressionScore: float
    emotionalStability: float
    healthRisk: float
    cognitiveScore: float
    overallRisk: float
    riskLevel: Literal["OBSERVE", "CAUTION", "WARNING", "HIGH"]
    summary: str
    recommendation: str

    @field_validator("depressionScore", "emotionalStability", "healthRisk", "cognitiveScore", "overallRisk")
    @classmethod
    def clamp_score(cls, v: float) -> float:
        # LLM이 0~10 범위를 벗어난 점수를 반환하는 경우 범위 내로 강제 조정
        return max(0.0, min(10.0, v))


class AdherenceRecord(BaseModel):
    adherenceType: Literal["MEDICATION", "MEAL"]
    slot: Literal["BREAKFAST", "LUNCH", "DINNER"]
    isNeeded: bool
    isDone: bool


class PhqResult(BaseModel):
    item_no: int
    score: Optional[int]
    risk_flag: bool
    needs_followup: bool
    confidence: str
    raw_quote: str
    note: str = ""
    source: str = "verbal"


class AnalyzeCallResponse(BaseModel):
    callLogId: int
    recipientId: int
    measuredAt: datetime
    assessment: AssessmentResult
    adherenceRecords: List[AdherenceRecord]
    phqResults: List[PhqResult] = []


class CallRequest(BaseModel):
    recipientId: int
    phoneNumber: str
    guardianPhoneNumber: str = ""


class CallResultsEntry(BaseModel):
    sequence: int
    question: str
    answer: str
    asked_at: datetime


class CallResultsRequest(BaseModel):
    """voice_agent.py가 통화 종료 후 /internal/report-call-results로 보내는 요청 바디.
    voice_agent.py는 이 스키마를 통해서만 main.py와 접점을 가진다(직접 import 없음)."""
    recipient_id: int
    started_at: datetime
    ended_at: datetime
    entries: List[CallResultsEntry]