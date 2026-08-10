"""인지 상태 지표 추출.

두 갈래로 인지 신호를 뽑는다.

1) ICOPE 객관 채점 — 3단어 회상, 시간·장소 지남력.
   정답을 서버가 알고 있으므로 라벨 데이터 없이 채점된다.
   (WHO ICOPE Step 1 인지 문항)

2) 언어 지표 — 어르신 발화의 형태소 구성 변화.
   DementiaBank Pitt corpus 연구에서 인지저하군과 정상군을 가르는 것으로
   보고된 지표들을 한국어에 옮긴 것이다. 보고된 방향은 다음과 같다.

     대명사 비율 ↑    명사 비율 ↓    어휘 다양성(TTR) ↓
     부사 비율 ↑      평균 발화 길이 ↓    감탄사 비율 ↑

   주의: 이 방향성은 영어 그림설명 과제에서 얻어진 것이라 한국어 전화 대화에
   그대로 성립한다는 보장이 없다. 그래서 절대 임계값으로 판정하지 않고,
   같은 어르신의 baseline 대비 편차(deviation_from_baseline)로만 쓴다.
   매일 같은 사람에게 전화하는 구조라 개인 baseline이 쌓이기 때문에
   "이 사람이 원래 어눌한가"가 아니라 "이 사람이 예전보다 나빠졌나"를 본다.
"""

import math
import re
import statistics
from datetime import date

from kiwipiepy import Kiwi

# 한국어는 교착어라 조사·어미가 토큰의 큰 비중을 차지한다.
# 영어 POS 연구와 비율을 비교 가능하게 만들려면 문법 형태소를 빼고
# 내용 형태소만 분모로 삼아야 한다.
_GRAMMATICAL_PREFIXES = ("J", "E", "S", "X")

# Kiwi 품사 태그
_NOUN_TAGS = {"NNG", "NNP", "NNB"}
_PRONOUN_TAGS = {"NP"}
_ADVERB_TAGS = {"MAG", "MAJ"}
_INTERJECTION_TAGS = {"IC"}

# 단어 인출 실패의 한국어 표지. "그… 저기… 뭐냐 그거"처럼
# 지시어와 간투사로 명사를 대체하는 패턴을 잡는다.
_DEMONSTRATIVES = {"그", "이", "저", "그거", "이거", "저거", "그것", "이것", "저것",
                   "거기", "여기", "저기", "그런", "이런", "저런"}
_FILLERS = {"어", "음", "아", "에", "그", "저", "뭐", "인제", "이제", "말이야", "뭐냐"}

# 지표를 신뢰할 수 있는 최소 발화량. 너무 짧으면 비율이 요동친다.
_MIN_RELIABLE_TOKENS = 30
_MATTR_WINDOW = 50

_kiwi: Kiwi | None = None


def _get_kiwi() -> Kiwi:
    global _kiwi
    if _kiwi is None:
        _kiwi = Kiwi()
    return _kiwi


# ---------------------------------------------------------------------------
# 1) ICOPE 객관 채점
# ---------------------------------------------------------------------------

def score_word_recall(answer: str, target_words: list[str]) -> dict:
    """3단어 회상 채점. 맞춘 개수를 그대로 반환한다.

    STT가 단어를 조사와 붙여 적거나("사과를") 살짝 잘못 받아적는 경우가 있어
    형태소 원형 일치까지 인정한다. 그 이상의 유사도 보정은 하지 않는다 —
    오답을 정답으로 세면 인지저하를 놓치는 쪽으로 편향되기 때문이다.
    """
    kiwi = _get_kiwi()
    lemmas = {t.form for t in kiwi.tokenize(answer)}
    normalized = re.sub(r"\s+", "", answer)

    recalled = [
        w for w in target_words
        if w in lemmas or re.sub(r"\s+", "", w) in normalized
    ]
    return {
        "recalled_count": len(recalled),
        "target_count": len(target_words),
        "recalled_words": recalled,
        "missed_words": [w for w in target_words if w not in recalled],
    }


def score_orientation(answer: str, asked_on: date) -> dict:
    """시간 지남력 채점. 서버가 아는 날짜를 정답으로 쓴다.

    요일·월·일 각각을 독립적으로 채점한다. 어르신이 셋 중 하나만 답해도
    맞춘 것은 맞춘 것으로 세고, 언급하지 않은 항목은 오답으로 세지 않는다.
    """
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    expected_weekday = weekdays[asked_on.weekday()]
    normalized = re.sub(r"\s+", "", answer)

    checks: dict[str, bool | None] = {}

    # 요일 — 답변에 어떤 요일이든 언급됐을 때만 채점 대상으로 삼는다
    mentioned = [d for d in weekdays if f"{d}요일" in normalized]
    checks["weekday"] = (expected_weekday in mentioned) if mentioned else None

    # 월 / 일
    month_match = re.search(r"(\d{1,2})\s*월", normalized)
    checks["month"] = (int(month_match.group(1)) == asked_on.month) if month_match else None

    day_match = re.search(r"(\d{1,2})\s*일", normalized)
    checks["day"] = (int(day_match.group(1)) == asked_on.day) if day_match else None

    answered = [v for v in checks.values() if v is not None]
    return {
        "checks": checks,
        "correct_count": sum(answered),
        "answered_count": len(answered),
        # 아무것도 답하지 않은 것 자체가 신호이므로 무응답을 별도로 표시한다
        "no_response": len(answered) == 0,
    }


# ---------------------------------------------------------------------------
# 2) 언어 지표
# ---------------------------------------------------------------------------

def _mattr(forms: list[str], window: int = _MATTR_WINDOW) -> float:
    """Moving-average TTR.

    단순 TTR은 발화가 길수록 낮아져서, 말수가 줄어든 것과 어휘가 빈약해진 것을
    구분하지 못한다. 고정 창으로 평균 내면 길이 영향이 제거된다.
    """
    if len(forms) < window:
        return len(set(forms)) / len(forms) if forms else 0.0
    ratios = [
        len(set(forms[i:i + window])) / window
        for i in range(len(forms) - window + 1)
    ]
    return statistics.fmean(ratios)


def _adjacent_repetition_ratio(utterances: list[list[str]]) -> float:
    """인접 형태소 반복 비율. "그 그 그거" 같은 되뇌임을 잡는다."""
    total = repeated = 0
    for forms in utterances:
        for prev, cur in zip(forms, forms[1:]):
            total += 1
            if prev == cur:
                repeated += 1
    return repeated / total if total else 0.0


def extract_linguistic_features(utterances: list[str]) -> dict:
    """어르신 발화 목록에서 언어 지표를 뽑는다.

    utterances: 한 통화에서 어르신(CUSTOMER)이 말한 발화 텍스트 목록.
    """
    kiwi = _get_kiwi()

    content_by_utt: list[list[str]] = []
    all_content: list[str] = []
    counts = {"noun": 0, "pronoun": 0, "adverb": 0, "interjection": 0,
              "demonstrative": 0, "filler": 0}
    sentence_lengths: list[int] = []

    for text in utterances:
        if not text or not text.strip():
            continue
        tokens = kiwi.tokenize(text)
        content = [t for t in tokens if not t.tag.startswith(_GRAMMATICAL_PREFIXES)]
        forms = [t.form for t in content]

        content_by_utt.append(forms)
        all_content.extend(forms)
        sentence_lengths.append(len(forms))

        for t in content:
            if t.tag in _NOUN_TAGS:
                counts["noun"] += 1
            elif t.tag in _PRONOUN_TAGS:
                counts["pronoun"] += 1
            elif t.tag in _ADVERB_TAGS:
                counts["adverb"] += 1
            elif t.tag in _INTERJECTION_TAGS:
                counts["interjection"] += 1

            if t.form in _DEMONSTRATIVES:
                counts["demonstrative"] += 1
            if t.tag in _INTERJECTION_TAGS and t.form in _FILLERS:
                counts["filler"] += 1

    total = len(all_content)
    if total == 0:
        return {"token_count": 0, "is_reliable": False}

    return {
        "token_count": total,
        "type_count": len(set(all_content)),
        "utterance_count": len(sentence_lengths),
        "mean_utterance_length": statistics.fmean(sentence_lengths),
        "ttr": len(set(all_content)) / total,
        "mattr": _mattr(all_content),
        "noun_ratio": counts["noun"] / total,
        "pronoun_ratio": counts["pronoun"] / total,
        "adverb_ratio": counts["adverb"] / total,
        "interjection_ratio": counts["interjection"] / total,
        "demonstrative_ratio": counts["demonstrative"] / total,
        "filler_ratio": counts["filler"] / total,
        "repetition_ratio": _adjacent_repetition_ratio(content_by_utt),
        # 발화량이 적으면 아래 비율들은 참고만 하고 판단 근거로 쓰지 않는다
        "is_reliable": total >= _MIN_RELIABLE_TOKENS,
    }


# ---------------------------------------------------------------------------
# 3) 개인 baseline 대비 편차
# ---------------------------------------------------------------------------

# 인지저하 방향으로 움직였다고 볼 부호. +1이면 값이 오를 때 악화,
# -1이면 값이 내려갈 때 악화.
_WORSENING_DIRECTION = {
    "pronoun_ratio": +1,
    "adverb_ratio": +1,
    "interjection_ratio": +1,
    "demonstrative_ratio": +1,
    "filler_ratio": +1,
    "repetition_ratio": +1,
    "noun_ratio": -1,
    "ttr": -1,
    "mattr": -1,
    "mean_utterance_length": -1,
}

# baseline으로 인정할 최소 통화 수. 이보다 적으면 편차를 계산하지 않는다.
_MIN_BASELINE_CALLS = 7


def deviation_from_baseline(current: dict, history: list[dict]) -> dict:
    """이번 통화 지표를 같은 어르신의 과거 통화들과 비교한다.

    history: 과거 통화들의 extract_linguistic_features 결과 목록.

    절대 임계값을 쓰지 않는 이유는 모듈 최상단 주석에 적어두었다.
    표준편차가 0인 지표(항상 같은 값)는 편차를 정의할 수 없으므로 건너뛴다.
    """
    usable = [h for h in history if h.get("is_reliable")]
    if not current.get("is_reliable") or len(usable) < _MIN_BASELINE_CALLS:
        return {
            "available": False,
            "reason": f"신뢰 가능한 과거 통화 {len(usable)}건 (최소 {_MIN_BASELINE_CALLS}건 필요)",
            "z_scores": {},
        }

    z_scores: dict[str, float] = {}
    new_signals: list[str] = []

    for key, direction in _WORSENING_DIRECTION.items():
        past = [h[key] for h in usable if key in h]
        if len(past) < _MIN_BASELINE_CALLS or key not in current:
            continue

        mean = statistics.fmean(past)
        sd = statistics.stdev(past)

        if math.isclose(sd, 0.0):
            # baseline에서 한 번도 변한 적 없는 지표. z를 정의할 수 없다.
            # 그렇다고 버리면 안 된다 — 지시어·간투사처럼 평소 0이던 값이
            # 처음 나타나는 것이야말로 우리가 찾는 신호이기 때문이다.
            if direction * (current[key] - mean) > 0:
                new_signals.append(key)
            continue

        # 부호를 통일해 양수 z가 항상 "악화 방향"을 뜻하게 만든다
        z_scores[key] = direction * (current[key] - mean) / sd

    if not z_scores and not new_signals:
        return {"available": False, "reason": "편차 계산 가능한 지표 없음", "z_scores": {}}

    worsening = {k: v for k, v in z_scores.items() if v >= 2.0}
    return {
        "available": True,
        "baseline_call_count": len(usable),
        "z_scores": z_scores,
        # baseline이 상수라 z를 못 매기지만 악화 방향으로 처음 나타난 지표
        "new_signals": new_signals,
        # 개별 지표는 흔들리므로 여러 지표가 동시에 움직였을 때만 신호로 본다
        "worsening_features": sorted(worsening, key=lambda k: -worsening[k]),
        "worsening_count": len(worsening) + len(new_signals),
        "mean_z": statistics.fmean(z_scores.values()) if z_scores else 0.0,
    }
