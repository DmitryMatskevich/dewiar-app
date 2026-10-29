# GigaAM (распознавание речи) на видеокарте для приложений Dewiar.
# Нужен NVIDIA Container Toolkit на хосте (как для vLLM). Без видеокарты тоже заработает — на процессоре.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 HF_HOME=/models
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir "onnxruntime-gpu>=1.19" "onnx-asr[hub]>=0.12" "numpy<2.3"
WORKDIR /srv
COPY app.py .
EXPOSE 8020
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8020/health')"
CMD ["python3", "app.py"]
