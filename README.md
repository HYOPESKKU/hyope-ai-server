# hyope-ai-server

HYOPE 서비스의 AI 분석 서버입니다.

Spring Boot 백엔드로부터 통화 Q&A 데이터를 받아 LLM으로 분석하고,
독거노인의 건강·정서·인지 지표와 복약·식사 이행 여부를 반환합니다.

## 역할

```
Spring Boot → POST /analyze-call (통화 Q&A 전달)
                    ↓
             GPT-4.1-mini 분석
                    ↓
Spring Boot ← 분석 결과 반환 (지표 점수 + adherenceRecords)
                    ↓
Spring Boot → DB 저장 (assessment, adherence_record 테이블)
```

직접 DB에 접근하거나 전화를 걸지 않습니다.
분석 요청을 받고 결과를 돌려주는 역할만 합니다.

## 분석 지표

| 필드 | 설명 | 방향 |
|---|---|---|
| `depressionScore` | 우울·외로움·무기력 신호 강도 | 높을수록 위험 |
| `emotionalStability` | 정서 안정 수준 | 높을수록 좋음 |
| `healthRisk` | 신체 건강 이상 징후 강도 | 높을수록 위험 |
| `cognitiveScore` | 인지기능 정상 수준 | 높을수록 좋음 |
| `overallRisk` | 종합 위험도 | 높을수록 위험 |
| `riskLevel` | OBSERVE / CAUTION / WARNING / HIGH | overallRisk 기준 서버 재계산 |

## 환경변수

`.env` 파일을 프로젝트 루트에 생성하세요.

```env
OPENAI_API_KEY=sk-...
```

| 변수 | 필수 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | OpenAI API 키. 없으면 서버 시작 시 즉시 에러 |

## 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# 서버 실행
uvicorn main:app --reload --port 8000
```

서버가 정상적으로 뜨면 `GET /` 에서 아래 응답이 옵니다.

```json
{ "status": "ok", "message": "HYOPE AI server is running" }
```

## API

### POST /analyze-call

통화 Q&A를 분석해 건강·정서 지표와 복약·식사 이행 여부를 반환합니다.

**Request**

```json
{
  "callLogId": 1,
  "recipientId": 42,
  "entries": [
    { "sequence": 1, "question": "오늘 아침 약 드셨나요?", "answer": "아, 깜빡했어요." },
    { "sequence": 2, "question": "요즘 기분은 어떠세요?", "answer": "좀 외롭긴 한데 괜찮아요." }
  ]
}
```

**Response**

```json
{
  "callLogId": 1,
  "recipientId": 42,
  "measuredAt": "2026-06-17T10:00:00",
  "assessment": {
    "depressionScore": 3.5,
    "emotionalStability": 6.0,
    "healthRisk": 1.0,
    "cognitiveScore": 8.0,
    "overallRisk": 3.0,
    "riskLevel": "CAUTION",
    "summary": "오늘 아침 약을 깜빡하셨다고 하셨습니다. 기분은 약간 외롭다고 하셨으나 전반적으로 무난한 상태입니다.",
    "recommendation": "보호자가 오늘 한 번 전화해보시길 권장합니다. 복약 여부를 다시 확인해주세요."
  },
  "adherenceRecords": [
    {
      "adherenceType": "MEDICATION",
      "slot": "BREAKFAST",
      "isNeeded": true,
      "isDone": false
    }
  ]
}
```

**에러**

| 코드 | 상황 |
|---|---|
| 400 | entries가 비어있거나 모든 answer가 빈 문자열 (무응답·통화 실패) |
| 500 | LLM 호출 실패 또는 3회 재시도 후에도 JSON 파싱 실패 |

## 파일 구조

```
hyope-ai-server/
├── main.py          # FastAPI 앱, /analyze-call 엔드포인트
├── llm_analyzer.py  # 프롬프트 정의, OpenAI 호출, 재시도 로직
├── schemas.py       # Pydantic 스키마 (Request / Response)
├── requirements.txt # 의존성 목록
└── .env             # 환경변수 (gitignore 처리됨, 직접 생성 필요)
```
