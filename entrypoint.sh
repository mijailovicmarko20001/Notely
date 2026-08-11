#!/bin/sh
# Container entrypoint: point the pipeline's hardcoded ROOT-relative paths
# (input/, output/, .env — siblings of scripts/) at the persistent volume.
set -e

mkdir -p /app/data/input/videos /app/data/input/slides /app/data/output /app/data/hf-cache
touch /app/data/.env

ln -sfn /app/data/input  /app/input
ln -sfn /app/data/output /app/output
ln -sf  /app/data/.env   /app/.env

exec uvicorn webui.main:app --host 0.0.0.0 --port 8000
