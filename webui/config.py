"""Paths and .env-backed settings for the Notely web UI.

Mirrors the stage scripts' convention: everything is relative to the project
root (the parent of this package), so the working directory never matters.
"""

import os
import re
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key

# Only needed to bootstrap the `from notely...` import below (finding
# notely/ on sys.path) -- notely.paths.PROJECT_ROOT is the same value and
# is what the rest of this file uses. Not guaranteed to already be on
# sys.path depending on how the server was launched (uvicorn
# webui.main:app vs. python -m webui.main vs. an IDE run config).
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

# SCRIPTS_DIR/SLIDES_DIR/VIDEOS_DIR aren't referenced in this file, only
# re-exported for other webui/ modules that do `from .config import
# SCRIPTS_DIR` etc. (see tests/webui/conftest.py's docstring for the full
# list) -- noqa since ruff can't see that cross-module usage.
from notely.env import (  # noqa: E402
    DEFAULT_OCR_LANG,
    DEFAULT_STAGE3_THRESHOLD,
    DEFAULT_WHISPER_BACKEND,
    DEFAULT_WHISPER_MODEL,
)
from notely.io import load_json_or_default  # noqa: E402
from notely.pipeline.matching import DEFAULT_AUTO_VISUAL_THRESHOLD  # noqa: E402
from notely.pipeline.visual_segment import DEFAULT_VISUAL_INTERVAL  # noqa: E402
from notely.paths import (  # noqa: E402, F401
    INPUT_DIR,
    OUTPUT_DIR,
    PROJECT_ROOT,
    SCRIPTS_DIR,
    SLIDES_DIR,
    VIDEOS_DIR,
)

LOGS_DIR = OUTPUT_DIR / "logs"
# Uploaded past exams (format templates for stage 11) and its generated
# output -- same "constant computed once, test fixture repoints it"
# convention as SLIDES_DIR/LOGS_DIR above.
EXAMS_DIR = INPUT_DIR / "exams"
OUTPUT_EXAMS_DIR = OUTPUT_DIR / "exams"
# Stage 9/10 output (notely.pipeline.essentials) -- same "constant computed
# once, test fixture repoints it" convention as EXAMS_DIR above.
OUTPUT_ESSENTIALS_DIR = OUTPUT_DIR / "essentials"
COURSE_ESSENTIALS_PATH = OUTPUT_DIR / "essentials.md"
# In Docker, /app/.env is a symlink into the persistent volume — but
# python-dotenv's set_key replaces the file atomically (temp file + rename),
# which would swap the symlink for a regular file inside the container.
# NOTELY_ENV_FILE points writes at the real file on the volume instead;
# stage scripts keep reading through the /app/.env symlink.
ENV_PATH = Path(os.environ.get("NOTELY_ENV_FILE") or PROJECT_ROOT / ".env")

VIDEO_URLS_PATH = INPUT_DIR / "video_urls.json"
LECTURES_META_PATH = INPUT_DIR / "lectures.json"

# Lecture ids are the trust boundary for every filesystem path built from
# user input (slide decks, review/timeline files, video files, job argv).
# Centralized here so every entry point (path params, form fields, JSON
# bodies) validates the same way instead of trusting Starlette's "no slash
# in a path segment" as the only guard.
LECTURE_ID_RE = re.compile(r"^lecture\d{2,}$")


def load_video_urls() -> dict:
    return load_json_or_default(VIDEO_URLS_PATH, {})


def validate_lecture_id(lecture_id: str, must_exist: bool = True) -> str:
    """Raise ValueError unless lecture_id is a well-formed id (and, by
    default, an id actually configured in video_urls.json)."""
    if not isinstance(lecture_id, str) or not LECTURE_ID_RE.match(lecture_id):
        raise ValueError(f"invalid lecture id: {lecture_id!r}")
    if must_exist and lecture_id not in load_video_urls():
        raise ValueError(f"unknown lecture id: {lecture_id!r}")
    return lecture_id


# Settings the UI exposes, with defaults. Course-specific defaults come from
# the validated lecture01 run (see CLAUDE.md / memory).
SETTING_KEYS = (
    "ANTHROPIC_API_KEY",
    "WHISPER_MODEL",
    "NOTES_MODEL",
    "OCR_LANG",
    "WHISPER_BACKEND",
    "GROQ_API_KEY",
    "GROQ_WHISPER_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_TRANSCRIBE_MODEL",
)

# Settings that are a credential, not a tuning knob -- masked on read
# (read_settings) and never persisted if the masked placeholder itself
# round-trips back on an unrelated save (write_settings). Was hardcoded
# to ANTHROPIC_API_KEY alone before cloud transcription added two more
# provider keys that need the exact same treatment.
SECRET_KEYS = ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY")

DEFAULT_STAGE_OPTIONS = {
    "crop": "0.12,0.06,0.63,0.88",  # stage 03 — Zoom capture of PDF viewer
    "threshold": DEFAULT_STAGE3_THRESHOLD,  # stage 03 -- D1, see notely.env
    "interval": 1.5,  # stage 03
    # stage 04 -- "deck" matches frames to slide numbers; "visual" ignores the
    # deck and segments by what's on screen, for recordings that don't present
    # slides (see notely.pipeline.visual_segment).
    "mode": "deck",
    # stage 04 -- deck mode: share of low-confidence matches that triggers an
    # automatic switch to visual segmentation (0 disables). See
    # notely.pipeline.matching.DEFAULT_AUTO_VISUAL_THRESHOLD.
    "auto_visual_threshold": DEFAULT_AUTO_VISUAL_THRESHOLD,
    "visual_threshold": 0.35,  # stage 04 -- visual mode only
    "visual_min_seconds": 45.0,  # stage 04 -- visual mode only
    # Stage 03's interval when visual mode is selected. Not a separate stage-3
    # flag: the UI writes it into the same `interval` field (see app.js's
    # syncIntervalForMode) so what runs is always what's shown.
    "visual_interval": DEFAULT_VISUAL_INTERVAL,
    "ocr_lang": DEFAULT_OCR_LANG,  # stage 04 -- D3, see notely.env
    "margin": 0.15,  # stage 04
    "stay_margin": 0.05,  # stage 04
    "confidence_threshold": 0.25,  # stage 04
    "min_forward_score": 0.05,  # stage 04
    "example_score_max": 0.12,  # stage 04 — worked-example detection
    "example_ink_delta": 12,  # stage 04 — worked-example detection
    "example_ink_text_overlap_min": 0.5,  # stage 04 — worked-example detection
    "example_ink_novel_word_min": 0.35,  # stage 04 — worked-example detection
    "min_dwell": 5.0,  # stage 05
}

DEFAULT_ENV = {
    "WHISPER_MODEL": DEFAULT_WHISPER_MODEL,  # D2, see notely.env
    "NOTES_MODEL": "claude-sonnet-5",
    "OCR_LANG": DEFAULT_OCR_LANG,  # D3, see notely.env
    "WHISPER_BACKEND": DEFAULT_WHISPER_BACKEND,  # see notely.env's own D1-D3-style comment
}


def read_settings(mask_key: bool = True) -> dict:
    """Current settings: .env values over defaults. Every SECRET_KEYS
    entry masked for display, each with its own has_*_key boolean so the
    UI can show "configured" without ever re-displaying the raw value."""
    values = {k: v for k, v in dotenv_values(ENV_PATH).items() if v is not None}
    settings = {**DEFAULT_ENV, **{k: v for k, v in values.items() if k in SETTING_KEYS}}
    settings["has_api_key"] = bool(settings.get("ANTHROPIC_API_KEY", ""))
    settings["has_groq_key"] = bool(settings.get("GROQ_API_KEY", ""))
    settings["has_openai_key"] = bool(settings.get("OPENAI_API_KEY", ""))
    if mask_key:
        for secret_key in SECRET_KEYS:
            v = settings.get(secret_key, "")
            settings[secret_key] = (v[:10] + "…") if v else ""
    return settings


# Slide-deck uploads stream to disk in fixed-size chunks (A3) rather than
# `await file.read()`-ing the whole thing into RAM -- matters under the
# memory-capped Docker/colima deployment. Configurable since course decks
# with heavily scanned/image slides can be large.
MAX_UPLOAD_BYTES = int(os.environ.get("NOTELY_MAX_UPLOAD_MB", "300")) * 1024 * 1024

MAX_SETTING_LENGTH = 4000  # generous for an API key; just bounds abuse
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")  # includes \n, \r -- see below


def write_settings(updates: dict) -> None:
    """Persist settings to .env without clobbering unrelated keys.

    Values land verbatim in .env (a `KEY=value` line per set_key), and
    stage_env() feeds .env straight into every stage subprocess's
    environment -- a newline in a value would inject an arbitrary extra
    line (e.g. smuggling in a second ANTHROPIC_API_KEY=... entry), so
    control characters and oversized values are rejected outright rather
    than sanitized.
    """
    ENV_PATH.touch(exist_ok=True)
    for k, v in updates.items():
        if k not in SETTING_KEYS or v is None:
            continue
        if k in SECRET_KEYS and (not v or v.endswith("…")):
            continue  # empty or masked value round-tripped from the UI
        v = str(v)
        if len(v) > MAX_SETTING_LENGTH:
            raise ValueError(f"{k}: value too long (max {MAX_SETTING_LENGTH} chars)")
        if _CONTROL_CHAR_RE.search(v):
            raise ValueError(f"{k}: control characters are not allowed")
        set_key(str(ENV_PATH), k, v, quote_mode="always")


def get_secret(key: str) -> str:
    """Read a credential the same way get_api_key() always has for
    ANTHROPIC_API_KEY: .env first, falling back to a real environment
    variable (e.g. already exported in Docker/CI)."""
    return dotenv_values(ENV_PATH).get(key) or os.environ.get(key, "")


def get_api_key() -> str:
    return get_secret("ANTHROPIC_API_KEY")


def stage_env() -> dict:
    """Environment for stage subprocesses: os.environ + .env values."""
    env = dict(os.environ)
    env.update({k: v for k, v in dotenv_values(ENV_PATH).items() if v is not None})
    env["PYTHONUNBUFFERED"] = "1"
    return env
