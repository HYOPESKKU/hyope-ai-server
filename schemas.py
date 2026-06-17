from pydantic import BaseModel, field_validator
from typing import List, Literal
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


class AnalyzeCallResponse(BaseModel):
    callLogId: int
    recipientId: int
    measuredAt: datetime
    assessment: AssessmentResult
    adherenceRecords: List[AdherenceRecord]