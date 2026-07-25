# HYOPE 개발 진행 현황

> 기준일: 2026-06-17 (마지막 업데이트: 2026-06-19)

---

## 개발 계획 대비 진행률

| 단계 | 기간 | 상태 | 진행률 |
|---|---|---|---|
| 1단계 기획·설계 | 4월 | ✅ 완료 | 100% |
| 2단계 핵심 파이프라인 구축 | 5~6월 | 🔧 진행 중 | ~30% |
| 3단계 앱 프론트엔드 개발 | 5~6월 | 🔧 진행 중 | ~10% |
| 4단계 통합 테스트 및 파일럿 운영 | 7~8월 | ⏳ 미시작 | - |
| 5단계 고도화 및 사업화 준비 | 8월 | ⏳ 미시작 | - |

---

## 2단계 체크리스트 — 핵심 파이프라인 (5~6월 마감)

### AI 백엔드 (류시현/홍승택)
- [x] FastAPI 서버 기본 구조 (`/analyze-call` 엔드포인트)
- [x] LLM 프롬프트 설계 (지표 추론, riskLevel, summary, recommendation)
- [x] 지표 산출 로직 구현 (depressionScore / emotionalStability / healthRisk / cognitiveScore / overallRisk)
- [x] adherenceRecords 후처리 (타입·슬롯 검증)
- [x] **프롬프트 개선** — 점수 기준 앵커 추가, 방향 명시, summary/recommendation 가이드, adherenceRecords 슬롯 규칙 (2026-06-17)
- [x] **AI 서버 안정성 강화** — 6개 항목 개선 (2026-06-17, 아래 변경 이력 참고)
- [ ] STT 연동 — claw-ops transcript API 활용 예정 (백엔드 담당, 보류)
- [ ] ARS → claw-ops 전화 발신 API 대체 (백엔드 담당)
- [ ] AI 서버 → Spring Boot 연동 테스트 (entries 포맷 협의 필요)
- [ ] 이전 통화 이력 컨텍스트 활용 (백엔드 협의 후)

### 백엔드 서버 (박진아)
- [x] 프로젝트 초기 설정 (2026-06-12 `3d4af9f`)
- [x] DB 구조 설계 — 엔티티 7개 (2026-06-12 `4ca148b`)
- [x] Repository 추가 (2026-06-13 `da850cd`)
- [x] DTO 추가 (Request/Response) (2026-06-13 `6c62edb`)
- [x] Service 생성 — Guardian, Recipient, CallSchedule (2026-06-14 `d0e1348`)
- [x] Service 추가 — CallLog, CallLogEntry, Assessment, AdherenceRecord (2026-06-14 `46000f3`)
- [x] Controller 추가 — 전체 7개 엔드포인트 (2026-06-15 `378875d`)
- [x] Assessment / Recipient 구조 보강 (2026-06-15 `35d2f51`)
- [x] Response DTO 수정 (2026-06-16 `d1f3b15`)
- [ ] ARS 자동 발신 스케줄러
- [ ] 통화 녹음 저장 모듈
- [ ] AI 서버 연동 (Spring Boot → `/analyze-call` 호출 후 결과 저장)

---

## 3단계 체크리스트 — 앱 프론트엔드 (5~6월 마감)

### 앱 (한태양)
- [ ] 보호자·복지사용 앱 UI/UX 설계
- [ ] 위험도 게이지 (0~10점, 색상 코드)
- [ ] 복약·식사 캘린더 (일별 O/X, 주간·월간)
- [ ] 영역별 지표 드롭다운
- [ ] 복지사용 대시보드 (위험도 순위)
- [ ] SMS 알림 및 앱 푸시 알림 연동

---

## 커밋 히스토리

### hyope-backend
| 날짜 | 커밋 | 내용 |
|---|---|---|
| 2026-06-12 | `3d4af9f` | 프로젝트 초기 설정 |
| 2026-06-12 | `4ca148b` | DB 구조 설계 (엔티티 7개) |
| 2026-06-13 | `da850cd` | Repository 추가 |
| 2026-06-13 | `6c62edb` | DTO 추가 (Request/Response) |
| 2026-06-14 | `d0e1348` | Service 생성 — Guardian, Recipient, CallSchedule |
| 2026-06-14 | `46000f3` | Service 추가 — CallLog, CallLogEntry, Assessment, AdherenceRecord |
| 2026-06-14 | `b3a116d` | README (ERD, 데이터 파이프라인) |
| 2026-06-15 | `35d2f51` | Assessment / Recipient 구조 보강 |
| 2026-06-15 | `378875d` | Controller 추가 (전체 API 완성) |
| 2026-06-15 | `b9939a9` | README 업데이트 |
| 2026-06-16 | `6e55bec` | 수정 |
| 2026-06-16 | `d1f3b15` | Response DTO 수정 |

### hyope-ai-server
| 날짜 | 커밋 | 내용 |
|---|---|---|
| 2026-06-14 | `883ed6c` | FastAPI LLM 분석 서버 구현 (최초) |
| 2026-06-17 | 미커밋 | 프롬프트 개선 + AI 서버 안정성 강화 (아래 변경 이력 참고) |

### hyope-ai
| 날짜 | 커밋 | 내용 |
|---|---|---|
| 2026-06-04 | - | Initial commit + README |

---

## 변경 이력

### 2026-06-17 — 프롬프트 개선 (`llm_analyzer.py`)

| 항목 | 변경 전 | 변경 후 | 이유 |
|---|---|---|---|
| 점수 기준 | 방향만 서술 | 0~2 / 3~5 / 6~8 / 9~10 구간별 예시 | LLM이 점수 기준을 모호하게 해석해 일관성이 낮았음 |
| 점수 방향 표시 | 없음 | `(높을수록 위험)` / `(높을수록 좋음)` 명시 | `emotionalStability`, `cognitiveScore`는 높을수록 좋은데 LLM이 반대로 해석하는 경우 있었음 |
| `overallRisk` 계산 기준 | "종합 위험도" 한 줄 | emotionalStability·cognitiveScore 낮으면 가중 규칙 명시 | 단순 평균으로 계산되어 인지 저하·정서 불안이 위험도에 반영 안 되던 문제 |
| `summary` | "string" | 발화 기반 2~3문장 + 예시 | 수치만 나열하는 무의미한 요약이 반환되던 문제 |
| `recommendation` | "string" | riskLevel별 행동 가이드 명시 | 위험 수준과 무관한 권고가 반환되던 문제 |
| `adherenceRecords` | BREAKFAST 2개 고정 | 언급된 슬롯만 생성, `isNeeded`·`isDone` 판단 기준 명시 | 통화에서 언급 안 된 슬롯까지 항상 생성해 오염 데이터 발생 |
| `temperature` | 없음 (기본값) | `temperature=0` | 동일 통화에 반복 호출 시 결과가 달라지는 문제 |

### 2026-06-17 — AI 서버 안정성 강화

| 파일 | 항목 | 변경 내용 | 이유 |
|---|---|---|---|
| `main.py` | slot 오류 처리 | 잘못된 slot → BREAKFAST 강제 대입 → **skip으로 변경** | BREAKFAST가 아닌 슬롯인데 BREAKFAST로 저장되면 DB 오염 |
| `schemas.py` | 점수 범위 검증 | `field_validator`로 모든 점수 필드 0~10 클리핑 추가 | LLM이 간헐적으로 범위 초과 값 반환 (예: 11점) |
| `main.py` | riskLevel 재계산 | `resolve_risk_level()` 추가, overallRisk 기준으로 항상 덮어씀 | LLM이 overallRisk=7인데 riskLevel="OBSERVE" 반환하는 불일치 발생 가능 |
| `llm_analyzer.py` | JSON 파싱 재시도 | 실패 시 최대 2회 재시도, 원문 포함 에러 | LLM이 마크다운 블록 감싸서 반환하는 경우 바로 500 에러 났던 문제 |
| `main.py` | 빈 통화 조기 처리 | 모든 answer가 빈 문자열이면 400 반환, LLM 호출 안 함 | 무응답·통화 실패 케이스에 불필요한 LLM 비용 발생 방지 |
| `main.py` | 환경변수 시작 검증 | `OPENAI_API_KEY` 없으면 서버 시작 시점에 즉시 에러 | 요청이 들어온 뒤에야 키 누락을 알게 되는 문제 |

---

## 남은 이슈 / 미결 사항

- [ ] **STT 연동** — claw-ops transcript API 활용 예정, 백엔드가 파싱 후 entries로 넘겨주는 방식 (인터페이스 협의 필요)
- [ ] **ARS → claw-ops 전환** — 전화 발신·녹음·STT 모두 claw-ops API로 대체 결정, 백엔드 담당
- [ ] **AI 서버 연동 테스트** — Spring Boot가 `/analyze-call` 호출 후 assessment 저장하는 흐름 미구현
- [ ] **sequence 0-base/1-base** — CallLogEntry sequence 시작값 컨벤션 미결정, 1-base 권장 (BE_issue4 참고)
- [ ] **이전 통화 이력 컨텍스트** — 최근 N건 assessment를 프롬프트에 포함하면 추이 기반 분석 가능 (백엔드 협의 후)
