"""webui/essentials.py: pure logic (no HTTP) for the Essentials tab's
state listing -- same separation/testing convention as
tests/webui/test_exams_pure.py's coverage of webui/exams.py."""

import json

import pytest

from webui import config, essentials


@pytest.fixture(autouse=True)
def _project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INPUT_DIR", tmp_path / "input")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(config, "VIDEO_URLS_PATH", tmp_path / "input" / "video_urls.json")
    monkeypatch.setattr(config, "OUTPUT_ESSENTIALS_DIR", tmp_path / "output" / "essentials")
    monkeypatch.setattr(config, "COURSE_ESSENTIALS_PATH", tmp_path / "output" / "essentials.md")
    (tmp_path / "input").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_urls(tmp_path, ids):
    (tmp_path / "input" / "video_urls.json").write_text(
        json.dumps({lid: f"https://youtu.be/{lid}" for lid in ids})
    )


# --- list_essentials_state -----------------------------------------------------


def test_list_essentials_state_empty_project():
    assert essentials.list_essentials_state() == {"lectures": [], "course_essentials": False}


def test_list_essentials_state_reports_no_notes_yet(tmp_path):
    _write_urls(tmp_path, ["lecture01"])

    state = essentials.list_essentials_state()

    assert state["lectures"] == [{"id": "lecture01", "has_notes": False, "has_essentials": False}]
    assert state["course_essentials"] is False


def test_list_essentials_state_reports_notes_but_no_essentials_yet(tmp_path):
    _write_urls(tmp_path, ["lecture01"])
    notes_dir = tmp_path / "output" / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "lecture01.md").write_text("# Lecture 1 notes")

    state = essentials.list_essentials_state()

    assert state["lectures"] == [{"id": "lecture01", "has_notes": True, "has_essentials": False}]


def test_list_essentials_state_reports_generated_essentials(tmp_path):
    _write_urls(tmp_path, ["lecture01"])
    notes_dir = tmp_path / "output" / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "lecture01.md").write_text("# Lecture 1 notes")
    config.OUTPUT_ESSENTIALS_DIR.mkdir(parents=True)
    (config.OUTPUT_ESSENTIALS_DIR / "lecture01.md").write_text("# Essentials")

    state = essentials.list_essentials_state()

    assert state["lectures"] == [{"id": "lecture01", "has_notes": True, "has_essentials": True}]


def test_list_essentials_state_reports_course_essentials(tmp_path):
    config.COURSE_ESSENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.COURSE_ESSENTIALS_PATH.write_text("# Essentials")

    state = essentials.list_essentials_state()

    assert state["course_essentials"] is True


def test_list_essentials_state_is_sorted_by_lecture_id(tmp_path):
    _write_urls(tmp_path, ["lecture02", "lecture01"])

    state = essentials.list_essentials_state()

    assert [lec["id"] for lec in state["lectures"]] == ["lecture01", "lecture02"]


def test_list_essentials_state_empty_essentials_file_is_not_reported_as_present(tmp_path):
    # a zero-byte file (e.g. an interrupted write) must not read as "done" --
    # same artifact_ok() convention (exists AND non-empty) every other stage uses.
    config.COURSE_ESSENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.COURSE_ESSENTIALS_PATH.write_text("")

    state = essentials.list_essentials_state()

    assert state["course_essentials"] is False
