"""통화 결과 → 위험도 점수 산출.

hyope-call-agent의 loader/worker.py에 있는 compute_assessment() 스텁을 대체한다.
그 함수는 "별도 채점 모델이 call_log_entries/logic_data를 보고 정규화된 점수를
내는 게 최종 그림"이라고 적힌 채 placeholder를 반환하고 있었다.

점수의 출처를 둘로 나눈다.

  1) 척도 판정 (logic_data) — 주 신호
     에이전트가 GDS-5 / GAD-2 / FRAIL·낙상 / SIS 문항을 물어보고 카테고리별로
     양호·우려·알수없음·위급 중 하나를 기록한 것. 검증된 임상 척도 기반이라
     이쪽이 주가 된다.

  2) 감정 분류기 — 보조 신호
     척도 문항 **바깥**에서 어르신이 흘린 자유 발화를 본다. 예를 들어
     "계단은 괜찮은데... 요새 사는 게 재미가 없어"처럼 신체 질문에 답하면서
     우울 신호를 흘리는 경우, 척도만으로는 못 잡는다.

     ⚠ 분류기 성능 한계를 전제로 가중치를 낮게 잡았다. 사람이 쓴 문장 기준
       신체불편 재현율 86.7%, 정서 3종은 F1 0.55~0.64다. 척도를 뒤집을 만한
       신뢰도가 아니라 어디까지나 보정용이다.
"""

from collections import Counter

# questions.json의 카테고리 → 분류기 라벨
CATEGORY_TO_LABEL = {"우울": "우울", "불안": "불안", "신체건강": "신체불편"}

# 판정 → 0~10 위험 점수. 척도 결과를 그대로 옮긴 값이다.
JUDGMENT_RISK = {"양호": 1.0, "우려": 6.0, "위급": 9.5, "알수없음": 5.0}

# 분류기가 점수를 움직일 수 있는 최대 폭(0~10 중). 척도가 주이고 분류기는 보조라
# 뒤집지 못하게 제한한다.
CLASSIFIER_WEIGHT = 2.0

# 이 확신 미만의 예측은 세지 않는다.
MIN_CONFIDENCE = 0.6

RISK_LEVELS = [(2, "OBSERVE"), (5, "CAUTION"), (7, "WARNING"), (10, "HIGH")]


def _judgment_risk(logic_data: dict, category: str) -> tuple[float, str | None]:
    block = logic_data.get(category) or {}
    j = block.get("judgment")
    return JUDGMENT_RISK.get(j, 5.0), j


def _label_ratios(predictions: list[dict]) -> dict[str, float]:
    """확신 있는 예측만 세어 라벨별 비율을 낸다."""
    conf = [p["emotion"] for p in predictions
            if p and not p.get("low_confidence") and p.get("confidence", 0) >= MIN_CONFIDENCE]
    if not conf:
        return {}
    c = Counter(conf)
    return {k: v / len(conf) for k, v in c.items()}


def _blend(base: float, ratio: float) -> float:
    """척도 점수에 분류기 비율을 보정으로 얹는다."""
    return max(0.0, min(10.0, base + CLASSIFIER_WEIGHT * ratio))


def _risk_level(overall: float) -> str:
    for bound, name in RISK_LEVELS:
        if overall <= bound:
            return name
    return "HIGH"


def compute(
    call_log_entries: list[dict],
    logic_data: dict,
    emergencies: list,
    predictions: list[dict],
) -> tuple[dict, list]:
    """(assessment, adherence_records)를 반환한다.

    predictions는 call_log_entries와 같은 길이로, 각 answer의 분류 결과다.
    """
    ratios = _label_ratios(predictions)

    dep_base, dep_j = _judgment_risk(logic_data, "우울")
    anx_base, anx_j = _judgment_risk(logic_data, "불안")
    hea_base, hea_j = _judgment_risk(logic_data, "신체건강")
    cog_base, cog_j = _judgment_risk(logic_data, "인지기능")

    depression = _blend(dep_base, ratios.get("우울", 0.0))
    health = _blend(hea_base, ratios.get("신체불편", 0.0))
    # emotional_stability는 "안정성"이라 위험과 방향이 반대다. 불안·분노가
    # 많을수록 낮아지고, 긍정이 많으면 올라간다.
    instability = _blend(anx_base, ratios.get("불안", 0.0) + ratios.get("분노", 0.0))
    stability = max(0.0, min(10.0, 10.0 - instability + 2.0 * ratios.get("긍정", 0.0)))
    # cognitive_score도 높을수록 좋은 지표다. 분류기는 인지를 못 보므로
    # SIS 판정만 쓴다(정답이 있는 객관 문항이라 분류기가 보탤 게 없다).
    cognitive = max(0.0, min(10.0, 10.0 - cog_base))

    # 종합 위험도 — 위험 방향으로 통일해서 평균낸다.
    overall = (depression + (10.0 - stability) + health + (10.0 - cognitive)) / 4.0
    if emergencies:
        # flag_emergency가 한 번이라도 불렸으면 최소 WARNING 아래로 못 내려간다.
        overall = max(overall, 7.5)

    level = _risk_level(overall)

    return {
        "measured_at": None,          # worker.py가 채운다
        "depression_score": round(depression, 1),
        "emotional_stability": round(stability, 1),
        "health_risk": round(health, 1),
        "cognitive_score": round(cognitive, 1),
        "overall_risk": round(overall, 1),
        "risk_level": level,
        "summary": _summary(logic_data, ratios, emergencies),
        "recommendation": _recommendation(level, logic_data, emergencies),
        # 근거를 함께 넘긴다. 점수만 보내면 보호자·복지사가 왜 그렇게 나왔는지
        # 확인할 방법이 없다.
        "evidence": {
            "judgments": {k: (v or {}).get("judgment") for k, v in logic_data.items()},
            "classifier_ratios": {k: round(v, 3) for k, v in ratios.items()},
            "classified_utterances": len([p for p in predictions if p]),
            "emergency_count": len(emergencies),
        },
    }, _adherence(logic_data)


def _summary(logic_data: dict, ratios: dict, emergencies: list) -> str:
    """사실만 나열한다. 문장 생성은 LLM 몫이라 여기서는 하지 않는다."""
    parts = []
    for cat, block in logic_data.items():
        j = (block or {}).get("judgment")
        if j in ("우려", "위급"):
            reason = (block or {}).get("reason", "")
            parts.append(f"{cat} {j}" + (f"({reason})" if reason else ""))
    if emergencies:
        parts.append(f"위급 신호 {len(emergencies)}건")
    top = [k for k, v in sorted(ratios.items(), key=lambda x: -x[1])
           if k != "긍정" and v >= 0.3]
    if top:
        parts.append(f"자유 발화에서 {'·'.join(top)} 신호 감지")
    return " / ".join(parts) if parts else "특이사항 없음"


def _recommendation(level: str, logic_data: dict, emergencies: list) -> str:
    if emergencies:
        return "위급 신호가 감지되었습니다. 즉시 어르신께 연락해 상태를 확인하세요."
    if level == "HIGH":
        return "여러 영역에서 위험이 확인됩니다. 방문 또는 전화로 직접 확인하시기 바랍니다."
    if level == "WARNING":
        return "우려 소견이 있습니다. 가까운 시일 내 안부 확인을 권장합니다."
    if level == "CAUTION":
        return "경미한 우려가 있습니다. 다음 통화에서 해당 영역을 다시 확인하겠습니다."
    return "특이사항 없이 안정적입니다."


def _adherence(logic_data: dict) -> list:
    """복약·식사 이행 기록.

    현재 questions.json의 medication/meal 문항은 예/아니오를 구조화해 남기지
    않고 카테고리 판정만 기록한다. 이행 여부를 정확히 뽑으려면 brain.py가
    문항별 답을 따로 저장해야 해서, 여기서는 빈 목록을 반환한다.
    ⚠ 미구현이며 팀 협의가 필요한 부분이다.
    """
    return []
