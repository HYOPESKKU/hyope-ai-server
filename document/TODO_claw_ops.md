# claw-ops 연동 TODO

## 현재 상태

`/analyze-call` 엔드포인트로 LLM 분석 자체는 동작 확인 완료.  
claw-ops API 포맷 미확정으로 `/call` 엔드포인트 구현 보류 중.

---

## 구현해야 할 것

### 1. `POST /call` 엔드포인트 추가 (`main.py`)

Spring Boot `CallScheduler`가 예약 시각마다 이 엔드포인트를 호출함.

```
Request (Spring → FastAPI):
{
  "recipientId": 1,
  "phoneNumber": "010-1234-5678"
}

Response: 200 OK (즉시 반환, 이후 백그라운드 처리)
```

### 2. 백그라운드 처리 함수 (`process_call`)

```
1. claw-ops API로 전화 발신          ← TODO: claw-ops 포맷 확인 필요
2. 통화 종료 후 STT transcript 수신   ← TODO: claw-ops 응답 포맷 확인 필요
3. transcript → entries 변환
4. LLM 분석 (기존 analyze_with_llm 재사용)
5. Spring POST /call-result 로 결과 전송
```

### 3. `POST /call-result` 콜백 전송 (`main.py`)

Spring이 받는 형식 (BE_issue6 기준):

```json
{
  "recipient_id": 1,
  "call_log": {
    "started_at": "...",
    "ended_at": "...",
    "status": "COMPLETED"
  },
  "call_log_entries": [
    { "sequence": 1, "question": "...", "answer": "...", "asked_at": "..." }
  ],
  "assessment": { ... },
  "adherence_records": [ ... ]
}
```

---

## claw-ops 확인 필요 사항

- 전화 발신 API 엔드포인트 및 요청 형식
- 통화 종료 이벤트 수신 방식 (webhook? polling?)
- STT transcript 응답 형식 (질문-답변 세트인지, 단순 텍스트인지)
- transcript → `entries` 변환 방식 논의 필요

---

## 참고

- Spring → FastAPI 호출 코드: `AiClientService.java`
- Spring이 결과 받는 엔드포인트: `POST /call-result` (`CallResultController.java`)
- BE 연동 가이드: `SUGGESTION.md`
