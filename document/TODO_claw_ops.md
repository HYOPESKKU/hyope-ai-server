# claw-ops 연동 TODO

## 현재 상태

| 항목 | 상태 |
|---|---|
| `/analyze-call` LLM 분석 | ✅ 완료 (로컬 테스트 완료) |
| `POST /call` 엔드포인트 | ✅ 스켈레톤 구현 완료 (`main.py`) |
| `POST /webhook/transcript` 엔드포인트 | ✅ 스켈레톤 구현 완료 (`main.py`) |
| transcript → entries 변환 | ✅ 구현 완료 (`claw_handler.py`) |
| Spring `/call-result` 전송 | ✅ 구현 완료 (`claw_handler.py`) |
| VoiceML 스크립트 엔드포인트 | ❌ 미구현 |
| 공개 webhook URL (ngrok 등) | ❌ 미설정 |
| claw-ops 계정 정보 `.env` 등록 | ❌ 미설정 |

---

## 남은 작업

### 1. VoiceML 스크립트 엔드포인트 (`POST /voiceml` 또는 별도 URL)

claw-ops가 전화를 걸 때 이 URL로 "어떤 질문을 할지" 스크립트를 가져간다.
어르신에게 물어볼 질문 목록을 VoiceML 형식으로 반환해야 한다.

```
CLAWOPS_VOICEML_URL=https://<public-url>/voiceml
```

FastAPI에 엔드포인트를 추가하거나 외부 URL로 제공해도 된다.

### 2. 공개 webhook URL 설정

claw-ops가 STT 완료 후 `/webhook/transcript`로 POST를 보내야 하므로,
AI 서버가 외부에서 접근 가능해야 한다.

로컬 개발 시 ngrok 사용:
```bash
ngrok http 8000
# 발급된 URL → .env에 등록
# WEBHOOK_BASE_URL=https://xxxx.ngrok.io
```

배포 환경에서는 실제 서버 URL로 대체한다.

### 3. `.env` 등록 필요 항목

```env
CLAWOPS_API_KEY=sk_...
CLAWOPS_ACCOUNT_ID=AC...
CLAWOPS_FROM_NUMBER=07052358010    # claw-ops 콘솔에서 확인
CLAWOPS_VOICEML_URL=https://...   # VoiceML 스크립트 URL
CLAWOPS_SIGNING_KEY=...           # 웹훅 서명 검증용 (선택)
WEBHOOK_BASE_URL=https://...      # 외부 접근 가능한 AI 서버 URL
SPRING_BASE_URL=http://localhost:8080
```

---

## 구현된 흐름

```
Spring CallScheduler
  → POST /call { recipientId, phoneNumber }
  → claw-ops calls.create() 전화 발신 (VoiceML 스크립트로 질문)
  → 통화 종료 후 claw-ops STT 처리
  → POST /webhook/transcript { CallId, TranscriptUrl, ... }
  → GCS에서 transcript JSON 다운로드
  → AGENT/CUSTOMER 세그먼트 → 질문-답변 쌍 변환
  → LLM 분석
  → Spring POST /call-result 전송
  → Spring DB 저장 완료
```

---

## 참고

- claw-ops 발신 API: `claw_handler.py` `initiate_call()`
- transcript 처리: `claw_handler.py` `handle_transcript()`
- Spring → FastAPI 호출 코드: `AiClientService.java`
- Spring 결과 수신 엔드포인트: `CallResultController.java`
