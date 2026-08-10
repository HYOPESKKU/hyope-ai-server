"""학습된 모델을 Hugging Face Hub에 올린다.

왜 HF인가:
  model.safetensors가 431MB라 git에 넣을 수 없다(.gitignore의 model_output*/).
  클라우드의 채점 서비스가 시작 시 받아가려면 어딘가 호스팅돼 있어야 하는데,
  HF Hub는 private repo가 무료이고 transformers의 from_pretrained가 repo id를
  그대로 받아서 코드 변경이 거의 없다.

체크포인트는 올리지 않는다:
  model_output/은 3GB인데 그중 2.5GB가 checkpoint-*/ 안의 옵티마이저 상태다.
  학습 재개용이라 추론에는 필요 없다. 431MB만 올린다.

사전 준비:
    pip install huggingface_hub
    huggingface-cli login     # Write 권한 토큰

사용법:
    python serve/upload_to_hf.py                          # 기본 repo
    python serve/upload_to_hf.py --repo 계정명/저장소명
    python serve/upload_to_hf.py --model-dir model_output_5label_v3_gen_20260810_1736
"""

import argparse
from pathlib import Path

from huggingface_hub import HfApi

# 추론에 필요한 파일만. 나머지는 학습 재개용이라 제외한다.
INFERENCE_FILES = [
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
]

DEFAULT_REPO = "HYOPESKKU/hyope-emotion-5"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--model-dir", default="model_output")
    ap.add_argument("--public", action="store_true",
                    help="공개 저장소로 만든다. 기본은 private.")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    model_dir = (root / args.model_dir).resolve()
    if not (model_dir / "config.json").exists():
        raise SystemExit(f"모델 디렉터리가 아니다: {model_dir}")

    present = [f for f in INFERENCE_FILES if (model_dir / f).exists()]
    total = sum((model_dir / f).stat().st_size for f in present)
    print(f"업로드 대상: {model_dir}")
    for f in present:
        print(f"  {f:<28}{(model_dir / f).stat().st_size / 1e6:>8.1f} MB")
    print(f"  합계 {total / 1e6:.1f} MB  (디렉터리 전체는 체크포인트 포함 3GB)")

    api = HfApi()
    who = api.whoami()["name"]
    print(f"\n계정: {who} / 저장소: {args.repo} ({'public' if args.public else 'private'})")

    api.create_repo(args.repo, private=not args.public, exist_ok=True)
    api.upload_folder(
        folder_path=str(model_dir),
        repo_id=args.repo,
        allow_patterns=INFERENCE_FILES,
        commit_message=f"upload {args.model_dir}",
    )
    print(f"\n완료: https://huggingface.co/{args.repo}")
    print("\n채점 서비스에 이렇게 넣는다:")
    print(f"  EMOTION_MODEL_PATH={args.repo}")
    print("  HF_TOKEN=<읽기 권한 토큰>   # private이면 필요")


if __name__ == "__main__":
    main()
