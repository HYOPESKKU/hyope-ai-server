import json
import os
from pathlib import Path

from openai import OpenAI

_PROMPT_PATH = Path(__file__).parent / "monthly.txt"

PHQ_QUESTIONS: dict[int, str] = {
    1: "지난 2주 동안, 일이나 여가 활동에서 흥미나 즐거움이 줄었나요?",
    2: "지난 2주 동안, 기분이 가라앉거나 우울하거나 희망이 없다고 느끼셨나요?",
    3: "지난 2주 동안, 잠들기 어렵거나 자주 깨거나 너무 많이 주무셨나요?",
    4: "지난 2주 동안, 피곤하거나 기운이 없다고 느끼셨나요?",
    5: "지난 2주 동안, 입맛이 없거나 반대로 너무 많이 드셨나요?",
    6: "지난 2주 동안, 자신이 형편없다고 느끼거나 가족을 실망시켰다는 생각이 드셨나요?",
    7: "지난 2주 동안, 신문이나 TV 집중하기가 어려우셨나요?",
    8: "지난 2주 동안, 평소보다 느리게 움직이거나 말씀하셨나요? 또는 반대로 안절부절못하셨나요?",
    9: "지난 2주 동안, 스스로 살기 싫다는 생각이나 자신을 해치고 싶다는 생각이 드신 적 있으셨나요?",
}

# STT 재전사 후 문항 매칭용 핵심 구절
_PHQ_KEYWORDS: dict[int, str] = {
    1: "흥미나 즐거움이 줄",
    2: "우울하거나 희망이 없",
    3: "잠들기 어렵거나",
    4: "피곤하거나 기운이 없",
    5: "입맛이 없거나",
    6: "형편없다고 느끼거나",
    7: "신문이나 TV",
    8: "느리게 움직이거나",
    9: "살기 싫다는 생각",
}


def identify_phq_item(agent_text: str) -> int | None:
    """AGENT 발화 텍스트에서 PHQ-9 문항 번호를 추출한다. 매칭 실패 시 None."""
    for item_no, keyword in _PHQ_KEYWORDS.items():
        if keyword in agent_text:
            return item_no
    return None


def classify_phq_item(
    item_no: int,
    transcript: str,
    is_followup_attempt: bool = False,
    model: str = "gemini-2.5-flash",
) -> dict:
    """정기검진.txt 프롬프트로 어르신 발화를 PHQ-9 점수(0~3)로 분류한다.

    반환: {"item_no", "score", "risk_flag", "needs_followup",
            "confidence", "raw_quote", "note"} + "source": "verbal"
    LLM/파싱 오류 시 score=null, needs_followup=True의 기본 dict 반환.
    """
    prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    client = OpenAI(
        api_key=os.getenv("GEMINI_API_KEY"),
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )
    user_msg = json.dumps(
        {
            "item_no": item_no,
            "item_question": PHQ_QUESTIONS[item_no],
            "transcript": transcript,
            "is_followup_attempt": is_followup_attempt,
        },
        ensure_ascii=False,
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
    except Exception as e:
        result = {
            "item_no": item_no,
            "score": None,
            "risk_flag": False,
            "needs_followup": True,
            "confidence": "low",
            "raw_quote": "",
            "note": f"분류 오류: {e}",
        }

    result["source"] = "verbal"
    return result
