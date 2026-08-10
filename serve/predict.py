"""학습된 모델로 감정을 분류한다. FastAPI 통합 시 이 클래스를 가져다 쓰면 된다."""
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from .config import CARE_SIGNAL_MAP, ID2LABEL, LABELS

# 이 값보다 확신이 낮으면 단일 라벨을 확정적으로 쓰지 않는다.
# 보호자에게 "우울입니다"라고 단정하는 것보다 상위 후보를 함께 보여주는 편이 낫다.
LOW_CONFIDENCE_THRESHOLD = 0.5


class EmotionPredictor:
    def __init__(self, model_path: str, device: str = "cpu"):
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_path)
        self.model.to(device)
        self.model.eval()
        self.device = device

    @staticmethod
    def _build_result(probs) -> dict:
        label_id = int(probs.argmax())
        confidence = float(probs[label_id])
        emotion = ID2LABEL[label_id]
        ranked = sorted(
            ((LABELS[i], float(p)) for i, p in enumerate(probs)),
            key=lambda x: -x[1],
        )
        return {
            "emotion": emotion,
            "confidence": round(confidence, 4),
            "scores": {name: round(p, 4) for name, p in ranked},
            "care_signals": CARE_SIGNAL_MAP.get(emotion, []),
            "top_emotions": [name for name, _ in ranked[:3]],
            "low_confidence": confidence < LOW_CONFIDENCE_THRESHOLD,
        }

    def predict(self, text: str) -> dict:
        """단일 텍스트 감정 분류.

        Returns:
            {
              "emotion": "우울",
              "confidence": 0.87,
              "scores": {"우울": 0.87, "불안": 0.06, ...},
              "care_signals": ["depression_related", "loneliness_check_recommended"],
              "top_emotions": ["우울", "불안", "신체불편"],
              "low_confidence": False
            }
        """
        inputs = self.tokenizer(
            text, return_tensors="pt",
            truncation=True, max_length=256,
        ).to(self.device)

        with torch.no_grad():
            logits = self.model(**inputs).logits

        probs = torch.softmax(logits, dim=-1)[0]
        return self._build_result(probs)

    def predict_batch(self, texts: list[str], batch_size: int = 32) -> list[dict]:
        """다수 텍스트 배치 처리."""
        results = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            inputs = self.tokenizer(
                batch, return_tensors="pt",
                truncation=True, padding=True, max_length=256,
            ).to(self.device)
            with torch.no_grad():
                logits = self.model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)
            results.extend(self._build_result(p) for p in probs)
        return results


# 직접 실행 시 간단한 테스트
if __name__ == "__main__":
    import sys

    model_path = sys.argv[1] if len(sys.argv) > 1 else "../model_output"
    predictor = EmotionPredictor(model_path)

    test_texts = [
        "요즘 너무 외롭고 힘들어요. 아무것도 하기 싫어요.",
        "오늘 손주가 놀러와서 정말 기분이 좋았어요.",
        "약을 먹어야 하는데 자꾸 깜빡해서 걱정돼요.",
        "무릎이 시큰거려서 오래 서 있질 못하겠어.",
    ]
    for text in test_texts:
        result = predictor.predict(text)
        print(f"입력: {text}")
        print(f"결과: {result['emotion']} (신뢰도: {result['confidence']})")
        print(f"돌봄 플래그: {result['care_signals']}")
        if result["low_confidence"]:
            print(f"⚠ 확신 낮음 — 상위 후보: {result['top_emotions']}")
        print()
