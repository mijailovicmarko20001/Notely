"""Environment/preflight checks, saved settings, and the API-key test --
the web UI's "Setup" tab."""

import logging

from fastapi import APIRouter, HTTPException

from notely.stages import MAX_PIPELINE_STAGE, STAGES
from .. import config, preflight
from ..models import SettingsUpdate

router = APIRouter(tags=["settings"])
log = logging.getLogger("notely.api")


@router.get("/preflight")
def get_preflight():
    settings = config.read_settings()
    return preflight.run_preflight(
        ocr_lang=settings.get("OCR_LANG", "eng"),
        whisper_model=settings.get("WHISPER_MODEL", "medium"),
        whisper_backend=settings.get("WHISPER_BACKEND", "faster-whisper"),
    )


@router.get("/settings")
def get_settings():
    return {
        "settings": config.read_settings(),
        "stage_defaults": config.DEFAULT_STAGE_OPTIONS,
        # The Run tab's stage checkboxes render from this (Phase 7 of the
        # cleanup plan) instead of hardcoding names/numbers in index.html,
        # which had drifted from the registry's own names (e.g. "Match
        # frames" vs "Match frames to slides"). Stops at MAX_PIPELINE_STAGE
        # (7): stage 8 (PDF export) isn't part of a pipeline run.
        "stages": [{"number": s.number, "name": s.name} for s in STAGES if s.number <= MAX_PIPELINE_STAGE],
    }


@router.put("/settings")
def put_settings(body: SettingsUpdate):
    try:
        config.write_settings(body.to_updates())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "settings": config.read_settings()}


def _test_provider_key(provider: str, key: str, ping) -> dict:
    """Shared shape for every provider below: 400 if no key is saved,
    otherwise a cheap real call to confirm it actually authenticates --
    {"ok": True} on success, {"ok": False, "error": ...} (never a raw
    exception/traceback) on any failure, with specifics only in the
    server log."""
    if not key:
        raise HTTPException(400, "no API key saved")
    try:
        ping()
        return {"ok": True}
    except Exception:
        log.exception("%s API key test failed", provider)
        return {"ok": False, "error": "key test failed — see server logs"}


@router.post("/settings/test-key")
def test_key(provider: str = "anthropic"):
    if provider == "anthropic":
        key = config.get_api_key()

        def ping():
            import anthropic

            anthropic.Anthropic(api_key=key, max_retries=1).messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=8,
                messages=[{"role": "user", "content": "ping"}],
            )
    elif provider == "groq":
        key = config.get_secret("GROQ_API_KEY")

        def ping():
            from groq import Groq

            Groq(api_key=key).models.list()  # cheapest real call that requires valid auth
    elif provider == "openai":
        key = config.get_secret("OPENAI_API_KEY")

        def ping():
            from openai import OpenAI

            OpenAI(api_key=key).models.list()
    else:
        raise HTTPException(422, f"unknown provider: {provider!r}")

    return _test_provider_key(provider, key, ping)
