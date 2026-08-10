"""서빙용 라벨 정의.

hyope-ai(학습 레포) train/config.py에서 라벨 부분만 떼어온 것이다.
학습 설정(데이터 경로·하이퍼파라미터)은 서빙에 필요 없어 제외했다.

⚠ 라벨 순서가 학습 때와 반드시 같아야 한다. 모델은 인덱스로 예측하므로
  순서가 어긋나면 조용히 틀린 라벨을 내놓는다. 재학습으로 라벨이 바뀌면
  이 파일도 함께 갱신할 것.
"""

# HYOPE 돌봄 라벨 5종.
#
# AI Hub 감성분류 6종(분노/기쁨/불안/당황/슬픔/상처)을 그대로 쓰지 않은 이유:
#   - "슬픔"과 "상처"는 나눠봐야 돌봄 조치가 같다 → 우울로 병합
#   - "당황"은 상황적 난처함이라 돌봄 위험 신호가 아니다 → 제외
#   - 정작 필요한 "신체불편"이 6종에 없다 → 신설
#
# 위험도 지표와의 연결:
#   우울 → depression_score   불안·분노 → emotional_stability
#   신체불편 → health_risk     긍정 → 전체 완충
LABELS = ["우울", "불안", "분노", "긍정", "신체불편"]
LABEL2ID = {l: i for i, l in enumerate(LABELS)}
ID2LABEL = {i: l for i, l in enumerate(LABELS)}

LABEL_DESCRIPTIONS = {
    "우울": "우울감, 외로움, 무기력, 소외감, 상실감",
    "불안": "불안, 걱정, 두려움, 초조함",
    "분노": "화남, 짜증, 억울함, 불만",
    "긍정": "기쁨, 안도, 만족, 편안함",
    "신체불편": "통증, 소화·수면 문제, 어지러움, 기력 저하 등 신체 증상 호소",
}

# 감정 라벨 → 보호자·복지사 확인이 필요한 돌봄 플래그.
CARE_SIGNAL_MAP = {
    "우울": ["depression_related", "loneliness_check_recommended"],
    "불안": ["anxiety_related", "care_check_recommended"],
    "분노": ["negative_emotion", "care_check_recommended"],
    "긍정": ["positive_emotion"],
    "신체불편": ["health_related", "physical_check_recommended"],
}
