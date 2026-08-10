# 감정 분류 + 위험도 채점 서비스.
# 빌드는 레포 루트에서:  docker build -f serve/Dockerfile -t hyope-classifier .
FROM python:3.12-slim

WORKDIR /app

# CPU 전용 torch 휠을 쓴다. 기본 PyPI 휠은 CUDA 런타임을 함께 받아서
# 이미지가 수 GB 불어나는데, 이 서비스는 짧은 문장 단건 추론이라
# CPU로 25 ms/건이면 충분하다.
COPY serve/requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple -r requirements.txt

# serve/ 는 자립 패키지다. predict.py와 config.py는 hyope-ai(학습 레포)에서
# 가져온 사본이라 학습 쪽이 바뀌면 함께 갱신해야 한다.
COPY serve/ ./serve/

# 모델은 이미지에 굽지 않는다. 432MB라 재배포가 느려지고, 모델만 갱신하고
# 싶을 때도 이미지를 다시 말아야 한다. 런타임에 HF Hub에서 받는다.
ENV EMOTION_MODEL_PATH=sean429/hyope-emotion-5 \
    EMOTION_DEVICE=cpu \
    HF_HOME=/models/.hf

RUN useradd --create-home --uid 10002 hyope \
    && mkdir -p /models && chown -R hyope:hyope /app /models
USER hyope

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request,json,sys; \
        d=json.load(urllib.request.urlopen('http://localhost:8001/health',timeout=3)); \
        sys.exit(0 if d.get('ok') else 1)"

CMD ["uvicorn", "serve.app:app", "--host", "0.0.0.0", "--port", "8001"]
