"""감정 분류 서빙 — call-agent가 통화 중 교차검증에 호출한다.

왜 별도 서비스인가:
  KoELECTRA 추론을 통화 에이전트(hyope-call-agent) 안에서 돌리면 torch와
  transformers가 실시간 음성 경로에 들어온다. 이미지가 ~2.5GB가 되고, 통화
  지연에 민감한 프로세스에 무거운 추론이 얹힌다. 분리해두면 이 서비스가
  죽어도 통화는 그대로 진행된다(call-agent 쪽이 실패를 무시하도록 돼 있다).

모델 출처는 EMOTION_MODEL_PATH로 지정한다. 로컬 경로도 되고 Hugging Face
repo id도 된다 — transformers의 from_pretrained가 둘 다 받는다.
private repo면 HF_TOKEN도 함께 넣어야 한다.
"""

import logging
import os
import time
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# predict.py와 config.py는 hyope-ai(학습 레포)에서 가져온 사본이다.
# 학습 때 쓴 추론 경로와 서빙 경로가 갈라지면 라벨 순서나 후처리가 어긋나므로
# 재구현하지 않고 복사했다. 학습 쪽이 바뀌면 여기도 같이 갱신해야 한다.
from . import assess as assess_mod
from .predict import EmotionPredictor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("classifier")

MODEL_PATH = os.environ.get("EMOTION_MODEL_PATH", "../model_output")
DEVICE = os.environ.get("EMOTION_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
MAX_TEXT_LEN = 2000

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 첫 요청 때 로딩하면 그 요청만 수 초씩 걸린다. 통화 중 호출이라 시작 시 미리 올린다.
    t = time.time()
    log.info("모델 로딩: %s (device=%s)", MODEL_PATH, DEVICE)
    predictor = EmotionPredictor(MODEL_PATH, device=DEVICE)
    labels = list(predictor.model.config.id2label.values())
    predictor.predict("무릎이 아파요")  # 워밍업 — 첫 추론이 유독 느리다
    log.info("로딩 완료 %.1fs / 라벨 %s", time.time() - t, labels)
    _state["predictor"] = predictor
    _state["labels"] = labels
    yield
    _state.clear()


app = FastAPI(title="HYOPE emotion classifier", lifespan=lifespan)


class ClassifyRequest(BaseModel):
    text: str = Field(..., description="어르신 발화 한 건")


class ClassifyBatchRequest(BaseModel):
    texts: list[str]


class AssessRequest(BaseModel):
    """hyope-call-agent의 loader/worker.py compute_assessment()가 보내는 것."""
    call_log_entries: list[dict] = Field(default_factory=list)
    logic_data: dict = Field(default_factory=dict)
    emergencies: list = Field(default_factory=list)


@app.get("/health")
def health() -> dict:
    ok = "predictor" in _state
    return {"ok": ok, "model_path": MODEL_PATH, "device": DEVICE,
            "labels": _state.get("labels", [])}


@app.post("/classify-emotion")
def classify(req: ClassifyRequest) -> dict:
    """단건 분류. 응답 형식은 train/predict.py의 EmotionPredictor.predict와 동일하다.

    emotion / confidence / scores / care_signals / top_emotions / low_confidence
    """
    if "predictor" not in _state:
        raise HTTPException(503, "모델 로딩 중")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "빈 텍스트")
    # STT가 통째로 긴 문자열을 보내는 사고를 대비. 모델은 어차피 256토큰에서 자른다.
    return _state["predictor"].predict(text[:MAX_TEXT_LEN])


@app.post("/classify-emotion/batch")
def classify_batch(req: ClassifyBatchRequest) -> dict:
    """일괄 분류 — 통화 종료 후 사후 분석용. 통화 중에는 단건을 쓴다."""
    if "predictor" not in _state:
        raise HTTPException(503, "모델 로딩 중")
    texts = [t.strip()[:MAX_TEXT_LEN] for t in req.texts if t and t.strip()]
    if not texts:
        return {"results": []}
    return {"results": _state["predictor"].predict_batch(texts)}


@app.post("/assess")
def assess(req: AssessRequest) -> dict:
    """통화 결과 → 위험도 점수. worker.py의 compute_assessment()를 대체한다.

    척도 판정(logic_data)이 주 신호이고 분류기는 척도 문항 바깥의 자유 발화를
    보는 보조 신호다. 분류기가 죽어도 척도만으로 점수가 나오도록 구성했다.
    """
    if "predictor" not in _state:
        raise HTTPException(503, "모델 로딩 중")

    answers = [(e.get("answer") or "").strip()[:MAX_TEXT_LEN] for e in req.call_log_entries]
    valid = [(i, a) for i, a in enumerate(answers) if a and a != "(무응답)"]

    predictions: list[dict | None] = [None] * len(answers)
    if valid:
        for (i, _), r in zip(valid, _state["predictor"].predict_batch([a for _, a in valid])):
            predictions[i] = r

    assessment, adherence = assess_mod.compute(
        req.call_log_entries, req.logic_data, req.emergencies, predictions
    )
    return {"assessment": assessment, "adherence_records": adherence}
