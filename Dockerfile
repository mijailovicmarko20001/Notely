# Notely — lecture videos + slides -> study notes, with a local web UI.
# Multi-arch (arm64 Mac / amd64 Windows-Linux), CPU-only.
FROM python:3.12-slim

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
