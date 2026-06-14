import json
import os
from openai import OpenAI
from dotenv import load_dotenv

from schemas import AnalyzeCallRequest

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


ANALYSIS_PROMPT = """
당신은 독거노인 AI 돌봄 서비스의 통화 분석 시스템입니다.

입력으로 어르신과 AI 상담 전화의 질문-응답 목록이 주어집니다.
당신의 목표는 의료 진단이 아니라, 보호자와 복지사가 참고할 수 있는 위험 신호를 선별하는 것입니다.

다음 항목을 0~10점 기준으로 평가하세요.

1. depressionScore: 우울/외로움/무기력 신호가 강할수록 높음
2. emotionalStability: 정서적으로 안정적일수록 높음
3. healthRisk: 통증, 낙상, 숨참, 어지러움, 수면 문제 등 건강 이상 신호가 강할수록 높음
4. cognitiveScore: 날짜, 장소, 기억 등 인지기능이 정상적일수록 높음
5. overallRisk: 종합 위험도. 위험할수록 높음

riskLevel은 overallRisk 기준으로 정하세요.
- 0~2: OBSERVE
- 3~5: CAUTION
- 6~7: WARNING
- 8~10: HIGH

복약과 식사 이행 여부도 판단하세요.
- medication isDone: 약을 먹었으면 true, 안 먹었거나 깜빡했으면 false
- meal isDone: 식사를 했으면 true, 안 했거나 거의 못 먹었으면 false

반드시 아래 JSON 형식으로만 답하세요.
설명 문장, markdown, 코드블록 없이 JSON만 반환하세요.

{
  "assessment": {
    "depressionScore": 0,
    "emotionalStability": 0,
    "healthRisk": 0,
    "cognitiveScore": 0,
    "overallRisk": 0,
    "riskLevel": "OBSERVE",
    "summary": "string",
    "recommendation": "string"
  },
  "adherenceRecords": [
    {
      "adherenceType": "MEDICATION",
      "slot": "BREAKFAST",
      "isNeeded": true,
      "isDone": true
    },
    {
      "adherenceType": "MEAL",
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


def analyze_with_llm(request: AnalyzeCallRequest) -> dict:
    transcript = build_transcript_text(request)

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
        ]
    )

    raw_text = response.output_text

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        raise ValueError(f"LLM 응답이 JSON 형식이 아닙니다: {raw_text}")