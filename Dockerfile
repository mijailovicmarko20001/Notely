# Notely — lecture videos + slides -> study notes, with a local web UI.
# Multi-arch (arm64 Mac / amd64 Windows-Linux). CPU-only by default; see the
# `gpu` stage at the bottom for opt-in NVIDIA acceleration.
FROM python:3.12-slim AS base

# JS runtime for yt-dlp's YouTube extraction (deno is its default-enabled
# runtime; without one, extraction is deprecated and formats go missing).
# denoland/deno:bin is multi-arch, so this stays arm64/amd64 compatible.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-srp-latn \
        libreoffice-impress \
        chromium \
        fonts-dejavu fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ scripts/
COPY webui/ webui/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# All persistent state (input/, output/, .env, whisper model cache) lives
# under /app/data, which docker-compose bind-mounts to ./data on the host.
ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/app/data/hf-cache \
    NOTELY_ENV_FILE=/app/data/.env

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]

# --- Opt-in NVIDIA GPU acceleration for stage 1 (transcription) -----------
# faster-whisper/ctranslate2 already default to device="auto", which picks
# CUDA automatically once it's actually visible -- no code change needed,
# just the CUDA runtime libraries ctranslate2 links against at runtime.
#
# Requires an NVIDIA GPU + nvidia-container-toolkit on the *host*, and the
# host must be Linux or Windows+WSL2 -- there is no GPU passthrough to
# Docker containers on macOS (Apple Silicon or Intel+eGPU), full stop.
# Build/run this stage via docker-compose.gpu.yml, which sets both the
# build target and the GPU device reservation: see that file's comments.
#
# pip-installed CUDA libraries (not an nvidia/cuda base image) so the
# default `base` image/build stays small and CPU-only for everyone who
# doesn't need this -- per faster-whisper's own documented GPU setup:
# https://github.com/SYSTRAN/faster-whisper#gpu
FROM base AS gpu
RUN pip install --no-cache-dir nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib
