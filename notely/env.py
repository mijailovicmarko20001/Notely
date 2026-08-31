"""Typed environment-variable reads, and the shared default values for
settings the CLI (scripts/*.py) and the web UI (webui/config.py) must
agree on.

D1-D3 (cleanup plan, Phase 2) were three cases where the two entry
points silently disagreed on a stage default -- a plain string/float
literal, independently copy-pasted into both places, drifted apart with
no error either way. Phase 2 fixed the *values*; this module removes the
duplication itself, so there is exactly one literal per setting instead
of two that a test has to keep honest (see
tests/test_cli_webui_defaults_agree.py, which still guards this from the
consumer side)."""

import os


def env_str(name: str, default: str) -> str:
    """String env var, falling back to `default` when unset or empty."""
    return os.environ.get(name) or default


def env_float(name: str, default: float) -> float:
    """Float env var, falling back to `default` when unset or empty."""
    value = os.environ.get(name)
    return float(value) if value else default


def env_bool(name: str, default: bool = False) -> bool:
    """Bool env var: "1" is True, anything else present is False, unset
    falls back to `default` -- the convention already used for
    NOTES_SEND_FRAME_IMAGE / NOTES_DETECT_EXAMPLES."""
    value = os.environ.get(name)
    return value == "1" if value is not None else default


# D1: stage 3's slide-change threshold -- 0.08 (a reasonable-looking
# generic default) detected almost nothing on this course's real Zoom
# recordings; 0.02 is what DOCUMENTATION.md's calibration table validates.
DEFAULT_STAGE3_THRESHOLD = 0.02

# D2: "small" mis-detects Serbian as Bosnian (see WHISPER_MODEL's use at
# scripts/01_transcribe.py); "medium" is the validated default.
DEFAULT_WHISPER_MODEL = "medium"

# D3: plain "eng" misses this course's Serbian-latin slide text/OCR
# almost entirely; "srp_latn+eng" is the validated default.
DEFAULT_OCR_LANG = "srp_latn+eng"
