## 개요

통화 중 어르신 상태를 파악하기 위한 5개 카테고리 설문 스키마(`우울`, `정서 안정`, `신체 건강`, `인지기능`, `복약/식사`)를 확정하고, 이를 AI 통화 에이전트의 질문 로직에 반영한다. 현재는 형식적인 질문 체크 로직만 추가된 상태라 자연스러운 대화보다는 설문 느낌이 강하게 나는 한계가 있어, 후속 작업으로 프롬프트/흐름 자연화가 필요하다.

## 설계 방향 — 질문 피로도 완화

매일 전화하는 서비스 특성상 매 통화마다 5개 카테고리 문항을 전부 물어보면 어르신 입장에서 피로도가 누적될 수 있다. 이를 완화하기 위해 카테고리별로 기본은 1문항만 질문하고(`min_questions: 1`), 그 응답이 `negative_signal`에 해당하면(이상 징후 의심) 그 카테고리 안에서만 나머지 문항을 추가로 물어 자세히 확인하는 방식으로 설계했다. 즉 평소엔 짧게, 이상 신호가 감지된 카테고리에 한해서만 깊게 들어가는 구조다. (`daily_check`처럼 예외적으로 매번 전 문항을 다 물어야 하는 카테고리는 `min_questions`를 2로 지정)

## 카테고리 및 근거

### 1. 우울 — Hoyl GDS-5
- 문항 5개 (근본적 생활 만족도, 지루함, 무력감, 외출 기피, 무가치감)
- 채점: 우울 방향 응답 2개 이상 → 우울 증상 있음으로 판단
- 참고문헌: 노인 환자에서 다양한 단축형 노인우울척도의 비교(GDS 비교 연구), *Development and testing of a five-item version of the Geriatric Depression Scale*

### 2. 정서 안정 — GAD-2
- 문항 2개, 0~3점 4단계 척도 (전혀 없음 ~ 거의 매일)
- 채점: 총점 0~6점, 3점 이상이면 불안장애 양성 스크리닝
- 참고문헌: *Anxiety disorders in primary care: prevalence, impairment, comorbidity, and detection*

### 3. 신체 건강 — 자기평가 건강 단일문항 + FRAIL + 낙상 게이트
- 자기평가 건강(5점 척도) 1문항 + FRAIL 5문항(피로, 계단, 보행, 질환, 체중감소) + 낙상 게이트 1문항
- 낙상 게이트가 "예"면 같은 통화 내에서 불안정감·낙상 우려 2문항을 조건부로 추가 시행
- 채점: FRAIL 3점 이상 **또는** 낙상 고위험 플래그 **또는** 자기평가 "매우 나쁨" 중 하나라도 해당 시 "신체 건강 이상 징후: 높음"
- 참고문헌: Idler & Benyamini(1997) 자기평가 건강, Morley et al.(2012) FRAIL Scale, CDC STEADI 낙상 게이트

### 4. 인지기능 — SIS (Six-Item Screener)
- Mini-Cog 대신 SIS 채택
  - Mini-Cog는 저작권자 허가 없는 변형(구성요소 제거 등)이 금지되어 있어 상업 서비스 적용 시 법적으로 애매함
  - SIS는 "3단어 회상 + 지남력" 조합 그대로 정식 검증된 절단점이 있음
  - SIS는 애초에 전화 인터뷰용으로 설계되어 통화 인터페이스에 적합
- 지연 회상(단어 3개) 3점 + 지남력(요일/월/연도) 3점 = 총 6점
- 채점: 총점 3점 이하(3개 이상 오류)면 인지장애 시사
- 회상 세트(`sis_encode` → `sis_recall`)는 반드시 쌍으로 진행, 정답 여부는 어르신께 언급하지 않고 항상 같은 톤 유지
- 참고문헌: *Six-item screener to identify cognitive impairment among potential subjects for clinical research*

### 5. 복약/식사 확인
- `medication`, `meal` 2문항, 예외 없이 매 통화마다 둘 다 질문
- 시간대별 자연스러운 진입 멘트(아침/점심/저녁/밤) 예시 포함

## 스키마 구조 (JSON)

카테고리별로 아래 필드를 갖는 구조로 정의:

- `name`: 척도명
- `instructions`
  - `min_questions`: 최소 질문 수
  - `negative_signal`: 부정적 응답으로 판단하는 기준
  - `on_negative`: 부정 신호 감지 시 처리 방침 — 다음 카테고리로 넘어가지 않고 해당 카테고리 내 나머지 문항을 추가 질문
  - `note`: 카테고리별 특이사항 (예: SIS의 encode/recall 쌍 진행, 톤 유지 지침 / daily_check의 예외 없는 필수 질문 및 시간대별 브릿지 멘트)
- `items`: 문항 배열
  - `id`, `question`
  - `type`: 질문/발화 구분(SIS의 `sis_encode`는 `statement`)
  - `pair_with`: 짝을 이루는 문항 id (SIS encode/recall)
  - `depends_on`: 조건부 문항 (신체 건강의 낙상 후속 문항 — `fall_gate`가 "예"일 때만 노출)

## 테스트 방법

```
uvicorn main:app --port 8000
```

PowerShell에서 통화 발신 테스트:

```powershell
$body = @{
    recipientId = 1
    phoneNumber = "01059334106"
    guardianPhoneNumber = "01099998888"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/call" `
    -Method Post `
    -ContentType "application/json" `
    -Headers @{ "X-API-KEY" = "vb2q9HqxWqg2X/CjmnRN5+o01SVHd7FaPO9C1R61Dhw=" } `
    -Body $body
```

## 현재 상태 / 한계

- 5개 카테고리 문항·채점 기준·스키마 정의는 완료
- 현재는 형식적인 질문 체크(필수 문항 소진 여부) 로직만 추가된 상태 → 자연스러운 대화보다 설문에 가까운 흐름으로 진행됨
- 후속 작업으로 대화 자연화(문항 간 전이 멘트, 톤 조정 등) 필요

## TODO

- [ ] 카테고리별 기본 1문항 질문 → `negative_signal` 감지 시에만 해당 카테고리 내 추가 질문하는 피로도 완화 로직 구현
- [ ] 카테고리별 `negative_signal` / `on_negative` 로직을 에이전트 프롬프트·툴에 반영
- [ ] SIS `sis_encode`/`sis_recall` 쌍 진행 및 매 통화 랜덤 단어 생성 로직 구현
- [ ] 신체 건강 카테고리 `depends_on`(낙상 게이트 조건부 문항) 처리 구현
- [ ] `daily_check` 시간대별 컨텍스트 브릿지 멘트 적용
- [ ] 형식적 질문 체크 로직 → 자연스러운 대화 흐름으로 개선
- [ ] 실통화 테스트로 각 카테고리 질문 흐름 검증