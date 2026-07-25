# AI 서버 연동 가이드 (Spring Boot)

## 흐름

통화 종료 → **Spring Boot가 AI 서버 호출** → 결과 받아서 DB 저장

---

## AI 서버 호출

```http
POST http://<ai-server>/analyze-call

{
  "callLogId": 1,
  "recipientId": 1,
  "entries": [
    { "sequence": 1, "question": "오늘 밥 드셨나요?", "answer": "응, 먹었어." },
    { "sequence": 2, "question": "오늘 약 드셨나요?", "answer": "아니, 안 먹었어." }
  ]
}
```

> `entries`는 `call_log_entry` 테이블의 sequence / question / answer 그대로 넣으면 됩니다.
> 통화 실패(FAILED / MISSED) 건은 호출하지 마세요 → 400 반환됩니다.

---

## AI 서버 응답 후 저장 순서

### 1. assessment 저장

응답의 `assessment.*` 필드를 꺼내서 저장합니다.

```http
POST /recipient/{recipientId}/call-log/{callLogId}/assessment

{
  "recipient_id": 1,
  "call_log_id": 1,
  "measuredAt": "2026-06-18T10:30:00",
  "depressionScore": 3.5,
  "emotionalStability": 6.0,
  "healthRisk": 1.0,
  "cognitiveScore": 8.0,
  "overallRisk": 3.0,
  "riskLevel": "CAUTION",
  "summary": "...",
  "recommendation": "..."
}
```

### 2. adherenceRecord 저장

저장된 assessmentId로 adherenceRecords 배열을 순서대로 저장합니다.

```http
POST /assessment/{assessmentId}/adherence-record

{
  "assessment_id": 1,
  "adherenceType": "MEDICATION",
  "slot": "BREAKFAST",
  "isNeeded": true,
  "isDone": false
}
```

---

## 에러 코드

에러 응답은 모두 아래 형식입니다.

```json
{ "detail": "에러 메시지" }
```

| 코드 | `detail` 예시 | 처리 |
|---|---|---|
| 400 | `"분석 가능한 답변이 없습니다. 통화 중 어르신의 응답이 확인되지 않았습니다."` | 저장 생략 |
| 500 | `"LLM이 3회 시도 후에도 JSON을 반환하지 않았습니다."` | 저장 생략, 재시도 고려 |
