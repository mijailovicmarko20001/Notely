"""Shared JSON/text I/O: load, and atomic (temp-file + rename) write.

Every stage script had its own copy of "write via a temp file then
rename" -- 4 near-identical load_json variants (scripts/04, 05, 07,
run_pipeline.py) and 8 hand-inlined/named atomic-write blocks (scripts/00,
01, 02, 03, 04, 05, 06 x2, 07 x2), several with subtly different (and in
two cases, genuinely buggy) encoding/ensure_ascii choices -- see D5 (Phase
2, the original UnicodeEncodeError-under-C-locale bug) and two follow-up
fixes found while consolidating these (stage 4's and stage 2's JSON
writes were both silently \\u-escaping this course's actual Serbian
content instead of writing it as UTF-8). One canonical implementation now:
UTF-8 throughout, ensure_ascii=False for JSON, atomic rename on every
write -- `path.exists() and size > 0` is exactly what
webui/progress.py::artifact_ok trusts to decide a stage is done and
skippable on the next run, so a killed process (SIGKILL, docker stop,
host crash) must never be able to leave a truncated-but-non-empty
artifact behind.
"""

import json
import os
from pathlib import Path
from typing import Any


def load_json(path) -> Any:
    """Load JSON from path, decoded as UTF-8."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_json_or_default(path, default: Any) -> Any:
    """Like load_json, but returns `default` instead of raising when the
    file doesn't exist yet or isn't valid JSON -- the shape every webui/
    settings/metadata file (video_urls.json, lectures.json, ...) needs
    before its first write. Was reimplemented near-identically four times
    (webui/config.py::load_video_urls, webui/routes/common.py::load_json,
    webui/decks.py::_load_json), each missing load_json's encoding="utf-8"
    (D5's bug class again -- see this module's docstring)."""
    try:
        return load_json(path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def write_text_atomic(path, text: str) -> None:
    """Write text to path via a temp file + atomic rename, UTF-8-encoded."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def save_json(path, data, indent: int = 2) -> None:
    """Write data as JSON to path via write_text_atomic (UTF-8,
    ensure_ascii=False -- see this module's docstring)."""
    write_text_atomic(path, json.dumps(data, indent=indent, ensure_ascii=False))
