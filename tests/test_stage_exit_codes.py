"""D4 (cleanup plan, Phase 2): stages 01-04 used to exit 0 even when they
skipped a lecture because a required input was missing (no video, no slide
deck, no frame events, no extracted slides) -- process_lecture/
transcribe_lecture printed a "[skip] ... not found" message and returned
None, and main() never looked at the return value, so run_pipeline.py's
`subprocess.run(...).returncode` (its only failure signal, per
webui/progress.py's own docstring: "Stages 01-04 exit 0 even when they skip
for missing input") saw success and happily proceeded to the next stage
with no input for it to work from.

Fixed by having each process_lecture/transcribe_lecture return False for a
missing-required-input skip (True for an already-done skip, which is a
legitimate no-op, and True after a real successful run), and having each
main() collect failures across the batch and sys.exit(1) if any occurred --
the exact pattern scripts/00_fetch_videos.py's main() already used before
this fix (`failures = [...]; if failures: sys.exit(1)`)."""

import sys

import pytest

from conftest import load_stage

s01 = load_stage("01_transcribe.py")
s02 = load_stage("02_extract_slides.py")
s03 = load_stage("03_detect_slide_changes.py")
s04 = load_stage("04_match_frames_to_slides.py")


@pytest.fixture()
def empty_project(tmp_path, monkeypatch):
    """Every stage's *_DIR constant repointed at an empty tmp tree -- no
    video, no slide deck, no frame events, nothing. These are already
    fully-resolved Path objects computed from PROJECT_ROOT at each module's
    import time, so they're repointed directly rather than via PROJECT_ROOT
    (which nothing re-derives paths from after import)."""
    monkeypatch.setattr(s01, "INPUT_VIDEOS_DIR", tmp_path / "input" / "videos")
    monkeypatch.setattr(s01, "OUTPUT_TRANSCRIPTS_DIR", tmp_path / "output" / "transcripts")
    monkeypatch.setattr(s02, "SLIDES_DIR", tmp_path / "input" / "slides")
    monkeypatch.setattr(s02, "OUTPUT_DIR", tmp_path / "output" / "slides_extracted")
    monkeypatch.setattr(s03, "INPUT_VIDEOS_DIR", tmp_path / "input" / "videos")
    monkeypatch.setattr(s03, "OUTPUT_DIR", tmp_path / "output" / "frame_events")
    monkeypatch.setattr(s04, "FRAME_EVENTS_DIR", tmp_path / "output" / "frame_events")
    monkeypatch.setattr(s04, "SLIDES_EXTRACTED_DIR", tmp_path / "output" / "slides_extracted")
    monkeypatch.setattr(s04, "OUTPUT_DIR", tmp_path / "output" / "slide_timelines")
    return tmp_path


def test_stage01_transcribe_lecture_returns_false_for_missing_video(empty_project):
    assert s01.transcribe_lecture("lecture01", model_size="medium") is False


def test_stage01_main_exits_nonzero_for_missing_video(empty_project, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["01_transcribe.py", "lecture01"])
    monkeypatch.setattr(s01, "load_dotenv_if_available", lambda: None)
    with pytest.raises(SystemExit) as exc_info:
        s01.main()
    assert exc_info.value.code != 0


def test_stage02_process_lecture_returns_false_for_missing_deck(empty_project):
    assert s02.process_lecture("lecture01", force=False) is False


def test_stage02_main_exits_nonzero_for_missing_deck(empty_project, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["02_extract_slides.py", "lecture01"])
    with pytest.raises(SystemExit) as exc_info:
        s02.main()
    assert exc_info.value.code != 0


def test_stage03_process_lecture_returns_false_for_missing_video(empty_project):
    assert s03.process_lecture("lecture01", interval=1.5, threshold=0.02, crop_str=None, force=False) is False


def test_stage03_main_exits_nonzero_for_missing_video(empty_project, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["03_detect_slide_changes.py", "lecture01"])
    with pytest.raises(SystemExit) as exc_info:
        s03.main()
    assert exc_info.value.code != 0


def test_stage04_process_lecture_returns_false_for_missing_frame_events(empty_project):
    assert s04.process_lecture("lecture01", margin=0.15, confidence_threshold=0.25, force=False) is False


def test_stage04_main_exits_nonzero_for_missing_frame_events(empty_project, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["04_match_frames_to_slides.py", "lecture01"])
    with pytest.raises(SystemExit) as exc_info:
        s04.main()
    assert exc_info.value.code != 0


# --- the "already done" skip is a success, not a failure --------------------


def test_stage02_process_lecture_returns_true_when_already_done_and_not_forced(empty_project):
    out_json = empty_project / "output" / "slides_extracted" / "lecture01.json"
    out_json.parent.mkdir(parents=True)
    out_json.write_text("[]")
    assert s02.process_lecture("lecture01", force=False) is True


def test_stage02_main_exits_zero_when_every_lecture_was_already_done(empty_project, monkeypatch):
    out_json = empty_project / "output" / "slides_extracted" / "lecture01.json"
    out_json.parent.mkdir(parents=True)
    out_json.write_text("[]")
    monkeypatch.setattr(sys, "argv", ["02_extract_slides.py", "lecture01"])
    # must not raise SystemExit at all -- a clean, unforced skip is success
    s02.main()
