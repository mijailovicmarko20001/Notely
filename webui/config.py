"""Paths and .env-backed settings for the Notely web UI.

Mirrors the stage scripts' convention: everything is relative to the project
root (the parent of this package), so the working directory never matters.
"""

import os
from pathlib import Path

from dotenv import dotenv_values, set_key

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"
VIDEOS_DIR = INPUT_DIR / "videos"
SLIDES_DIR = INPUT_DIR / "slides"
LOGS_DIR = OUTPUT_DIR / "logs"
# In Docker, /app/.env is a symlink into the persistent volume — but
# python-dotenv's set_key replaces the file atomically (temp file + rename),
# which would swap the symlink for a regular file inside the container.
# NOTELY_ENV_FILE points writes at the real file on the volume instead;
# stage scripts keep reading through the /app/.env symlink.
ENV_PATH = Path(os.environ.get("NOTELY_ENV_FILE") or PROJECT_ROOT / ".env")

VIDEO_URLS_PATH = INPUT_DIR / "video_urls.json"
LECTURES_META_PATH = INPUT_DIR / "lectures.json"

# Settings the UI exposes, with defaults. Course-specific defaults come from
# the validated lecture01 run (see CLAUDE.md / memory).
SETTING_KEYS = ("ANTHROPIC_API_KEY", "WHISPER_MODEL", "NOTES_MODEL", "OCR_LANG")

DEFAULT_STAGE_OPTIONS = {
    "crop": "0.12,0.06,0.63,0.88",   # stage 03 — Zoom capture of PDF viewer
    "threshold": 0.02,                # stage 03
    "interval": 1.5,                  # stage 03
    "ocr_lang": "srp_latn+eng",      # stage 04
    "margin": 0.15,                   # stage 04
    "stay_margin": 0.05,              # stage 04
    "confidence_threshold": 0.25,     # stage 04
    "min_forward_score": 0.05,        # stage 04
    "min_dwell": 5.0,                 # stage 05
}

DEFAULT_ENV = {
    "WHISPER_MODEL": "medium",  # "small" mis-detected Serbian as Bosnian
    "NOTES_MODEL": "claude-sonnet-5",
    "OCR_LANG": "srp_latn+eng",
}


def read_settings(mask_key: bool = True) -> dict:
    """Current settings: .env values over defaults. API key masked for display."""
    values = {k: v for k, v in dotenv_values(ENV_PATH).items() if v is not None}
    settings = {**DEFAULT_ENV, **{k: v for k, v in values.items() if k in SETTING_KEYS}}
    key = settings.get("ANTHROPIC_API_KEY", "")
    settings["has_api_key"] = bool(key)
    if mask_key:
        settings["ANTHROPIC_API_KEY"] = (key[:10] + "…") if key else ""
    return settings


def write_settings(updates: dict) -> None:
    """Persist settings to .env without clobbering unrelated keys."""
    ENV_PATH.touch(exist_ok=True)
    for k, v in updates.items():
        if k not in SETTING_KEYS or v is None:
            continue
        if k == "ANTHROPIC_API_KEY" and (not v or v.endswith("…")):
            continue  # empty or masked value round-tripped from the UI
        set_key(str(ENV_PATH), k, str(v), quote_mode="never")


def get_api_key() -> str:
    return dotenv_values(ENV_PATH).get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")


def stage_env() -> dict:
    """Environment for stage subprocesses: os.environ + .env values."""
    env = dict(os.environ)
    env.update({k: v for k, v in dotenv_values(ENV_PATH).items() if v is not None})
    env["PYTHONUNBUFFERED"] = "1"
    return env
