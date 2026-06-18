# [AI] 로컬 통합 테스트 완료 및 claw-ops 연동 스켈레톤 구현 (2026-06-19)

## 테스트 환경

| 항목 | 내용 |
|---|---|
| AI 서버 | FastAPI + Gemini 2.5 Flash (OpenAI → Gemini 전환) |
| 백엔드 | Spring Boot 3.4.1 (port 8080) |
| DB | MySQL 8.0 + hyope_db 더미데이터 |

---

## 테스트 결과

### AI 서버 단독 테스트

더미 통화 데이터 (`callLogId=1`, `recipientId=1`, entries 3건) 기준으로 `/analyze-call` 호출.

**입력 (dummy.sql 기반 entries)**

| sequence | question | answer |
|---|---|---|
| 1 | 오늘 밥 드셨나요? | 응, 먹었어. |
| 2 | 오늘 약 드셨나요? | 아니, 안 먹었어. |
| 3 | 오늘 기분 어떠세요? | 일찍 일어났더니 피곤해서 기분이 좀 안 좋은 거 같아. |

**출력**

```json
{
  "callLogId": 1,
  "recipientId": 1,
  "measuredAt": "2026-06-19T04:24:14.636948",
  "assessment": {
    "depressionScore": 4.0,
    "emotionalStability": 5.0,
    "healthRisk": 3.0,
    "cognitiveScore": 9.0,
    "overallRisk": 4.0,
    "riskLevel": "CAUTION",
    "summary": "오늘 아침밥은 드셨으나 약은 드시지 않았다고 합니다. 일찍 일어나 피곤해서 기분이 좋지 않다고 말씀하셨습니다.",
    "recommendation": "어르신께 전화하여 약 복용 여부와 현재 기분 상태를 다시 한번 확인해 보시는 것을 권장합니다."
  },
  "adherenceRecords": [
    { "adherenceType": "MEAL", "slot": "BREAKFAST", "isNeeded": true, "isDone": true },
    { "adherenceType": "MEDICATION", "slot": "BREAKFAST", "isNeeded": true, "isDone": false }
  ]
}
```

**검증 항목**

- `riskLevel` — 약 누락 + 피곤함 → CAUTION 적절한 판정 ✅
- `summary` — 수치가 아닌 실제 발화 기반으로 생성 ✅
- `recommendation` — riskLevel에 맞는 보호자 행동 권고 ✅
- `adherenceRecords` — MEAL(완료), MEDICATION(미완) 정확히 구분 ✅
- 점수 클리핑 (0~10), riskLevel 서버 재계산 모두 정상 동작 ✅

### Spring Boot 연동 확인

- Spring Boot 기동 및 더미데이터 삽입 완료
- `GET /guardian/1`, `GET /call-log/1/call-log-entry/all` 정상 응답 확인 ✅
- AI 서버 응답 구조가 `AiCallResultDto` 필드와 일치 확인 ✅

---

## LLM 변경 사항

OpenAI `gpt-4.1-mini` → Gemini `gemini-2.5-flash`로 전환했습니다.

- OpenAI 호환 API(`https://generativelanguage.googleapis.com/v1beta/openai/`) 사용으로 코드 변경 최소화
- Gemini가 응답을 ` ```json ``` ` 블록으로 감싸는 경우 자동 제거 로직 추가
- 분석 품질은 동등 수준으로 확인

---

## claw-ops 연동 스켈레톤 구현

claw-ops webhook 문서 확인 후 전체 파이프라인 스켈레톤을 구현했습니다.

### 추가된 엔드포인트

| 엔드포인트 | 역할 |
|---|---|
| `POST /call` | Spring CallScheduler로부터 전화 요청 수신 → claw-ops 발신 |
| `POST /webhook/transcript` | claw-ops STT 완료 웹훅 수신 → LLM 분석 → Spring 전송 |

### 구현된 흐름

```
Spring → POST /call
       → claw-ops calls.create() (VoiceML 스크립트로 질문)
       → STT 완료 시 POST /webhook/transcript
       → GCS transcript 다운로드
       → AGENT/CUSTOMER 세그먼트 → 질문-답변 쌍 변환
       → LLM 분석
       → Spring POST /call-result → DB 저장
```

### 실제 통화 전 남은 작업

| 항목 | 설명 |
|---|---|
| VoiceML 스크립트 URL | 어르신에게 물어볼 질문 스크립트 — 미구현 |
| 공개 webhook URL | claw-ops가 AI 서버로 webhook을 보낼 수 있는 외부 URL (ngrok 등) |
| `.env` 계정 정보 등록 | `CLAWOPS_API_KEY`, `CLAWOPS_ACCOUNT_ID`, `CLAWOPS_FROM_NUMBER` 등 |

상세 내용은 `document/TODO_claw_ops.md` 참고.

---

## 피드백 요청

- VoiceML 질문 스크립트 구성 방식에 대해 논의 부탁드립니다. 질문 목록을 하드코딩할지, DB에서 가져올지 결정이 필요합니다.
- Spring `CallScheduler`의 AI 서버 호출 실패 시 fallback 처리 방식을 논의 부탁드립니다.
