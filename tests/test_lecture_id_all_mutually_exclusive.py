"""Stages 02 and 05 accepted `<lecture_id> --all` together and silently used
--all, ignoring the given lecture_id -- the same defect the plan calls out at
02:197 ("missing 'both' check"), which stages 00/01/03/04/06 already guard
against with `bool(args.all) == bool(args.lecture_id)` (or an equivalent
pair of checks). A course with a lecture named the same as some unrelated
positional typo would silently process the *whole* course instead of
erroring."""

import sys

import pytest

from conftest import load_stage

s02 = load_stage("02_extract_slides.py")
s05 = load_stage("05_segment_transcript.py")


def test_stage02_main_rejects_lecture_id_and_all_together(tmp_path, monkeypatch):
    slides_dir = tmp_path / "slides"
    slides_dir.mkdir()
    (slides_dir / "lecture01.pdf").write_bytes(b"")
    monkeypatch.setattr(s02, "SLIDES_DIR", slides_dir)
    monkeypatch.setattr(s02, "process_lecture", lambda lecture_id, force=False: True)
    monkeypatch.setattr(sys, "argv", ["02_extract_slides.py", "lecture01", "--all"])

    with pytest.raises(SystemExit) as exc_info:
        s02.main()
    assert exc_info.value.code != 0


def test_stage05_main_rejects_lecture_id_and_all_together(tmp_path, monkeypatch):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "video_urls.json").write_text('{"lecture01": "https://youtu.be/x"}')
    monkeypatch.setattr(s05, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(s05, "segment_transcript", lambda lecture_id, min_dwell=5.0, force=False: True)
    monkeypatch.setattr(sys, "argv", ["05_segment_transcript.py", "lecture01", "--all"])

    with pytest.raises(SystemExit) as exc_info:
        s05.main()
    assert exc_info.value.code != 0
