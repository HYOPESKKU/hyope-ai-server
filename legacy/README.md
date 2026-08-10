# legacy — call-agent 이전 구현

통화 발신·진행·분석을 이 서버가 직접 하던 시절의 코드다. 통화 로직이
`hyope-call-agent`로 옮겨가면서 대부분 대체됐다. **삭제하지 않고 보관만 한다** —
Spring 연동 스펙이나 프롬프트를 참고할 일이 남아 있다.

## 무엇이 무엇으로 대체됐나

| 파일 | 대체 |
|---|---|
| `claw_handler.py` | call-agent `server.py` + `agent/brain.py`. claw-ops 발신과 통화 진행 |
| `voiceml.py` | 위와 동일. VoiceML/TwiML 스크립트 방식은 realtime 음성으로 바뀜 |
| `dummy_entries.py` | call-agent의 브라우저 데모(`static/index.html`) |
| `phq_classifier.py` | call-agent `static/questions.json`. PHQ-9 → GDS-5 + GAD-2 (노인 특화 척도) |
| `llm_analyzer.py` | `serve/assess.py`. Gemini 단독 분석 → 척도 판정 + 감정 분류기 |
| `main.py` | `serve/app.py`. `/analyze-call` → `/assess` |
| `schemas.py` | `serve/app.py`의 Pydantic 모델 |

## 현재 살아있는 코드

```
serve/                  FastAPI — /health /classify-emotion /assess
cognitive_features.py   SIS 객관 채점 + 언어 지표 (아직 미연동)
```

## 왜 통째로 옮겼나

파일들이 서로 얽혀 있어 일부만 뺄 수 없었다. `main.py`가 6개를 모두 import하고,
`claw_handler`·`voiceml`도 서로를 참조한다. 하나만 빼면 나머지가 깨진다.

## 되살리려면

플랫 import(`from schemas import ...`)를 쓰므로 이 폴더 안에서 실행해야 한다.

```bash
cd legacy && uvicorn main:app --port 8000
```
