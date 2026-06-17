import json
import os
from openai import OpenAI
from dotenv import load_dotenv

from schemas import AnalyzeCallRequest

load_dotenv()

# 모듈 로드 시점이 아닌 실제 호출 시점에 초기화 — 시작 시 키 검증은 main.py에서 담당
def _get_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


ANALYSIS_PROMPT = """
당신은 독거노인 AI 돌봄 서비스의 통화 분석 시스템입니다.

입력으로 어르신과 AI 상담 전화의 질문-응답 목록이 주어집니다.
당신의 목표는 절대로 의료 진단이 아니라, 보호자와 복지사가 참고할 수 있는 위험 신호를 선별하는 것입니다.

## 평가 지표 (0~10점)

1. depressionScore — 우울·외로움·무기력 신호 강도 (높을수록 위험)
   - 0~2: 밝고 긍정적, 우울 신호 없음
   - 3~5: 약간 쓸쓸하거나 피곤함 언급, 대체로 무난
   - 6~8: 외로움·무기력·슬픔 표현 반복
   - 9~10: "살기 싫다" 등 극단적 표현, 완전한 사회적 단절

2. emotionalStability — 정서 안정 수준 (높을수록 좋음)
   - 0~2: 매우 불안하거나 감정 기복이 심함
   - 4~6: 보통 수준, 다소 걱정되거나 평범한 하루
   - 8~10: 차분하고 일관된 정서, 긍정적으로 마무리

3. healthRisk — 신체 건강 이상 징후 강도 (높을수록 위험)
   - 0~2: 특이 증상 없음, 건강하다고 표현
   - 3~5: 가벼운 피로, 약한 통증 언급
   - 6~8: 통증·낙상·어지러움·수면 문제 명확히 언급
   - 9~10: 호흡 곤란, 심한 낙상, 즉각 대응이 필요한 증상

4. cognitiveScore — 인지기능 정상 수준 (높을수록 좋음)
   - 0~2: 날짜·장소 혼동, 반복 발화, 이야기가 앞뒤 안 맞음
   - 4~6: 보통, 가끔 헷갈려하지만 전반적으로 이해 가능
   - 8~10: 날짜·상황 정확히 인지, 대화 맥락 유지

5. overallRisk — 종합 위험도 (높을수록 위험)
   depressionScore와 healthRisk를 주로 반영하되,
   emotionalStability 또는 cognitiveScore가 3 이하이면 위험도를 높이세요.

riskLevel은 overallRisk 기준으로 결정하세요.
- 0~2: "OBSERVE"
- 3~5: "CAUTION"
- 6~7: "WARNING"
- 8~10: "HIGH"

## summary (보호자가 읽을 2~3문장)
수치가 아닌 실제 발화 내용을 바탕으로 오늘 어르신 상태를 구체적으로 요약하세요.
예시: "오늘 아침밥은 드셨으나 약을 깜빡하셨다고 했습니다. 무릎이 약간 아프다고 하셨으며 기분은 대체로 괜찮다고 하셨습니다."

## recommendation (보호자 행동 권고, 1~2문장)
riskLevel에 맞게 구체적인 행동을 안내하세요.
- OBSERVE: 이상 없음 안내, 다음 통화에서 재확인 권고
- CAUTION: 전화 권장, 추가 확인이 필요한 항목 명시
- WARNING: 전화 및 방문 고려, 주의 이유 명시
- HIGH: 즉시 연락 권고, 긴급 대응이 필요한 이유 명시

## adherenceRecords 작성 규칙
통화에서 언급된 복약·식사 슬롯만 포함하세요. 언급되지 않은 슬롯은 생성하지 마세요.
- adherenceType: "MEDICATION" 또는 "MEAL"
- slot: "BREAKFAST", "LUNCH", "DINNER" 중 해당하는 것
- isNeeded: 해당 슬롯에 복약·식사가 필요한 대상자이면 true. 대상자가 명확히 해당 없다고 하면 false
- isDone: 이행했으면 true, 안 했거나 깜빡했으면 false, 답변이 불분명하면 false

## 출력 규칙
설명 문장, markdown, 코드블록 없이 아래 JSON 형식만 반환하세요.
adherenceRecords는 통화에서 언급된 슬롯 수에 맞게 가변적으로 작성하세요.

{
  "assessment": {
    "depressionScore": 0,
    "emotionalStability": 0,
    "healthRisk": 0,
    "cognitiveScore": 0,
    "overallRisk": 0,
    "riskLevel": "OBSERVE",
    "summary": "...",
    "recommendation": "..."
  },
  "adherenceRecords": [
    {
      "adherenceType": "MEDICATION",
      "slot": "BREAKFAST",
      "isNeeded": true,
      "isDone": true
    }
  ]
}
"""


def build_transcript_text(request: AnalyzeCallRequest) -> str:
    lines = []

    for entry in sorted(request.entries, key=lambda x: x.sequence):
        lines.append(f"Q{entry.sequence}. AI 질문: {entry.question}")
        lines.append(f"A{entry.sequence}. 어르신 답변: {entry.answer}")

    return "\n".join(lines)


def analyze_with_llm(request: AnalyzeCallRequest, max_retries: int = 2) -> dict:
    transcript = build_transcript_text(request)

    client = _get_client()
    last_raw = ""
    for attempt in range(max_retries + 1):
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": ANALYSIS_PROMPT
                },
                {
                    "role": "user",
                    "content": f"다음 통화 내용을 분석하세요.\n\n{transcript}"
                }
            ],
            temperature=0,
        )

        last_raw = response.output_text

        try:
            return json.loads(last_raw)
        except json.JSONDecodeError:
            # LLM이 마크다운 블록이나 설명 문장을 붙여 반환하는 경우 재시도
            if attempt < max_retries:
                continue

    raise ValueError(
        f"LLM이 {max_retries + 1}회 시도 후에도 JSON을 반환하지 않았습니다.\n응답 원문: {last_raw}"
    )