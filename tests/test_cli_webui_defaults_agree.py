"""D1-D3 (cleanup plan, Phase 2): the CLI (scripts/*.py) and the web UI
(webui/config.py) used to silently disagree on shared stage default
settings -- stage 3's slide-change threshold was 4x apart (0.02 vs 0.08),
plus the whisper model size and tesseract OCR language -- so the exact same
lecture produced different frame events / transcript / OCR text depending
on which entry point ran it, with no error or warning either way.

webui/config.py's defaults are the ones DOCUMENTATION.md documents as
tuned/validated against this course's actual recordings (e.g. "stage-03
threshold: 0.02, tuned for these recordings (defaults detected almost
nothing)"), so these tests pin the CLI scripts to match the web UI, not the
other way around.

Each test calls the real script's main() with a bare `<lecture_id>` argv
(no override flags) and a stubbed-out pipeline function, so the exact
default value that reaches the stage is captured without actually running
the stage."""

import sys

from conftest import load_stage
from webui import config

s01 = load_stage("01_transcribe.py")
s03 = load_stage("03_detect_slide_changes.py")
s04 = load_stage("04_match_frames_to_slides.py")


def test_stage03_threshold_default_matches_webui(monkeypatch):
    captured = {}
    monkeypatch.setattr(s03, "process_lecture", lambda lecture_id, **kw: captured.update(kw))
    monkeypatch.setattr(sys, "argv", ["03_detect_slide_changes.py", "lecture01"])

    s03.main()

    assert captured["threshold"] == config.DEFAULT_STAGE_OPTIONS["threshold"]


def test_stage01_whisper_model_default_matches_webui(monkeypatch):
    captured = {}
    monkeypatch.setattr(s01, "transcribe_lecture", lambda lecture_id, **kw: captured.update(kw))
    monkeypatch.setattr(s01, "load_dotenv_if_available", lambda: None)  # don't let a real .env leak in
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    monkeypatch.setattr(sys, "argv", ["01_transcribe.py", "lecture01"])

    s01.main()

    assert captured["model_size"] == config.DEFAULT_ENV["WHISPER_MODEL"]


def test_stage04_ocr_lang_default_matches_webui(monkeypatch):
    captured = {}
    monkeypatch.setattr(s04, "process_lecture", lambda lecture_id, **kw: captured.update(kw))
    monkeypatch.delenv("OCR_LANG", raising=False)
    monkeypatch.setattr(sys, "argv", ["04_match_frames_to_slides.py", "lecture01"])

    s04.main()

    assert captured["ocr_lang"] == config.DEFAULT_ENV["OCR_LANG"]
