from pydantic import BaseModel
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