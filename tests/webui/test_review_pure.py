"""review.apply_corrections -- pure timeline-editing logic behind the
Review tab's "apply corrections" flow. Exercised directly (no HTTP) since
it's a pure function once OUTPUT_DIR is pointed at the fixture tree."""

import json

import pytest

from webui import review


def _write_timeline(project_root, lecture_id, entries, notes=None):
    path = project_root / "output" / "slide_timelines" / f"{lecture_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"timeline": entries, "notes": notes or []}))
    return path


def _read_timeline(project_root, lecture_id):
    path = project_root / "output" / "slide_timelines" / f"{lecture_id}.json"
    return json.loads(path.read_text())


def test_apply_corrections_updates_slide_number_and_sets_confidence_to_1(project_root):
    _write_timeline(project_root, "lecture01", [
        {"start": 0, "end": 10, "slide_number": 1, "confidence": 0.9},
        {"start": 10, "end": 20, "slide_number": 2, "confidence": 0.4},
        {"start": 20, "end": 30, "slide_number": 3, "confidence": 0.9},
    ])
    result = review.apply_corrections("lecture01", [{"timestamp": 15, "slide_number": 5}])
    assert result == {"applied": 1, "timeline_entries": 3}

    data = _read_timeline(project_root, "lecture01")
    corrected = data["timeline"][1]
    assert corrected["slide_number"] == 5
    assert corrected["confidence"] == 1.0
    assert any("slide 2 -> 5" in n for n in data["notes"])


def test_apply_corrections_null_target_merges_into_previous_entry(project_root):
    _write_timeline(project_root, "lecture01", [
        {"start": 0, "end": 10, "slide_number": 1, "confidence": 0.9},
        {"start": 10, "end": 20, "slide_number": 2, "confidence": 0.4},
        {"start": 20, "end": 30, "slide_number": 3, "confidence": 0.9},
    ])
    result = review.apply_corrections("lecture01", [{"timestamp": 15, "slide_number": None}])
    assert result == {"applied": 1, "timeline_entries": 2}

    timeline = _read_timeline(project_root, "lecture01")["timeline"]
    assert timeline == [
        {"start": 0, "end": 20, "slide_number": 1, "confidence": 0.9},
        {"start": 20, "end": 30, "slide_number": 3, "confidence": 0.9},
    ]


def test_apply_corrections_null_target_on_first_entry_merges_into_next(project_root):
    _write_timeline(project_root, "lecture01", [
        {"start": 0, "end": 10, "slide_number": 1, "confidence": 0.9},
        {"start": 10, "end": 20, "slide_number": 2, "confidence": 0.4},
        {"start": 20, "end": 30, "slide_number": 3, "confidence": 0.9},
    ])
    result = review.apply_corrections("lecture01", [{"timestamp": 5, "slide_number": None}])
    assert result == {"applied": 1, "timeline_entries": 2}

    timeline = _read_timeline(project_root, "lecture01")["timeline"]
    assert timeline[0] == {"start": 0, "end": 20, "slide_number": 2, "confidence": 0.4}


def test_apply_corrections_merges_adjacent_entries_sharing_slide_number(project_root):
    _write_timeline(project_root, "lecture01", [
        {"start": 0, "end": 10, "slide_number": 1, "confidence": 0.9},
        {"start": 10, "end": 20, "slide_number": 2, "confidence": 0.5},
        {"start": 20, "end": 30, "slide_number": 1, "confidence": 0.9},
    ])
    # correcting the middle entry to slide 1 makes all three adjacent and
    # same-numbered -- they should collapse into a single entry
    result = review.apply_corrections("lecture01", [{"timestamp": 15, "slide_number": 1}])
    assert result == {"applied": 1, "timeline_entries": 1}

    timeline = _read_timeline(project_root, "lecture01")["timeline"]
    assert timeline == [{"start": 0, "end": 30, "slide_number": 1, "confidence": 1.0}]


def test_apply_corrections_ignores_timestamp_outside_any_window(project_root):
    entries = [
        {"start": 0, "end": 10, "slide_number": 1, "confidence": 0.9},
        {"start": 10, "end": 20, "slide_number": 2, "confidence": 0.4},
    ]
    _write_timeline(project_root, "lecture01", list(entries))
    result = review.apply_corrections("lecture01", [{"timestamp": 1000, "slide_number": 5}])
    assert result == {"applied": 0, "timeline_entries": 2}
    assert _read_timeline(project_root, "lecture01")["timeline"] == entries


def test_apply_corrections_writes_atomically_no_leftover_tmp_file(project_root):
    _write_timeline(project_root, "lecture01", [
        {"start": 0, "end": 10, "slide_number": 1, "confidence": 0.9},
    ])
    review.apply_corrections("lecture01", [{"timestamp": 5, "slide_number": 2}])
    timeline_dir = project_root / "output" / "slide_timelines"
    leftovers = [p for p in timeline_dir.iterdir() if ".tmp" in p.name]
    assert leftovers == []


def test_apply_corrections_rejects_invalid_lecture_id(project_root):
    with pytest.raises(ValueError):
        review.apply_corrections("../../evil", [{"timestamp": 5, "slide_number": 2}])


def test_get_review_data_raises_filenotfound_without_timeline(project_root):
    with pytest.raises(FileNotFoundError):
        review.get_review_data("lecture01")


def test_count_low_confidence_zero_when_no_review_file(project_root):
    assert review.count_low_confidence("lecture01") == 0
