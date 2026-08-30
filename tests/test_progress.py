"""webui/progress.py: stage stdout line parsing and artifact-existence
success checks. These are what the scheduler (webui/jobs.py) trusts to
decide whether a stage actually did anything and whether it's safe to skip
on a later run — worth pinning down with real tests rather than only
ad-hoc manual checks."""

from webui import progress


# --- parse_line ------------------------------------------------------------


def test_parse_line_ytdlp_download_percent():
    pct = progress.parse_line(0, "[download]  42.5% of 120.00MiB at 3.2MiB/s", {})
    assert pct == 0.425


def test_parse_line_ytdlp_ignores_unrelated_lines():
    assert progress.parse_line(0, "[youtube] Extracting URL", {}) is None


def test_parse_line_whisper_segment_needs_duration():
    line = "[00:10.000 --> 00:12.500]  some transcribed text"
    # no video_duration in ctx -> can't compute a fraction
    assert progress.parse_line(1, line, {"video_duration": None}) is None


def test_parse_line_whisper_segment_with_duration():
    line = "[10.000 -> 12.500] some transcribed text"
    pct = progress.parse_line(1, line, {"video_duration": 100.0})
    assert pct == 0.125


def test_parse_line_mlx_segment_format():
    # mlx-whisper's verbose format, past the 1-hour mark (H:MM:SS)
    line = "[1:02:03.000 --> 1:05:00.000] tekst predavanja"
    pct = progress.parse_line(1, line, {"video_duration": 10000.0})
    expected_end = 1 * 3600 + 5 * 60 + 0.0
    assert pct == min(expected_end / 10000.0, 1.0)


def test_parse_line_mlx_segment_under_one_hour():
    line = "[00:10.000 --> 00:20.000] text"
    pct = progress.parse_line(1, line, {"video_duration": 100.0})
    assert pct == 0.2


def test_parse_line_clamps_to_one():
    # a segment end past the (possibly slightly-off) known duration
    # shouldn't report progress over 100%
    line = "[0.0 -> 999.0]"
    pct = progress.parse_line(1, line, {"video_duration": 100.0})
    assert pct == 1.0


def test_parse_line_stage3_event_timestamp():
    pct = progress.parse_line(3, "[event 012] t=  45.00s -> slide 4", {"video_duration": 90.0})
    assert pct == 0.5


def test_parse_line_stage4_ocr_fraction():
    pct = progress.parse_line(4, "  [ocr 3/12] event_003.png", {})
    assert pct == 3 / 12


def test_parse_line_stage6_notes_fraction():
    pct = progress.parse_line(6, "  [7/20] slide 7: generated (in=1200 out=300)", {})
    assert pct == 7 / 20


def test_parse_line_indeterminate_stages_return_none():
    # stages 2/5/7 have no percent signal — spinner territory
    assert progress.parse_line(2, "[lecture01] wrote 42 slides", {}) is None
    assert progress.parse_line(5, "[lecture01] canonicalized 3 entries", {}) is None
    assert progress.parse_line(7, "Study guide assembled", {}) is None


# --- artifact_ok / stage_artifact ------------------------------------------


def test_artifact_ok_false_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(progress, "INPUT_DIR", tmp_path / "input")
    monkeypatch.setattr(progress, "OUTPUT_DIR", tmp_path / "output")
    assert progress.artifact_ok(1, "lecture99_does_not_exist") is False


def test_artifact_ok_false_when_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(progress, "INPUT_DIR", tmp_path / "input")
    monkeypatch.setattr(progress, "OUTPUT_DIR", tmp_path / "output")
    p = progress.stage_artifact(1, "lecture01")
    p.parent.mkdir(parents=True)
    p.touch()  # exists but zero bytes -- e.g. a killed process's leftover
    assert progress.artifact_ok(1, "lecture01") is False


def test_artifact_ok_true_when_present_and_nonempty(monkeypatch, tmp_path):
    monkeypatch.setattr(progress, "INPUT_DIR", tmp_path / "input")
    monkeypatch.setattr(progress, "OUTPUT_DIR", tmp_path / "output")
    p = progress.stage_artifact(6, "lecture01")
    p.parent.mkdir(parents=True)
    p.write_text("# lecture01\n\nsome note content")
    assert progress.artifact_ok(6, "lecture01") is True


def test_stage_artifact_stage7_has_no_lecture_id(monkeypatch, tmp_path):
    monkeypatch.setattr(progress, "OUTPUT_DIR", tmp_path / "output")
    assert progress.stage_artifact(7) == tmp_path / "output" / "study_guide.md"
