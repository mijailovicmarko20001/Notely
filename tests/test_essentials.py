"""Stage [9]/[10]: essentials distillation -- written before
notely.pipeline.essentials exists (test-first, per the approved plan).

Stage 9 (process_lecture_essentials) distills one lecture's finished notes
(output/notes/<lecture_id>.md) into a short must-know sheet
(output/essentials/<lecture_id>.md). Stage 10 (process_course_essentials)
aggregates the assembled study guide plus every stage-9 sheet into one
output/essentials.md, modeled on notely.pipeline.assemble's
generate_topic_index second-LLM-pass shape.

Both write outside output/notes/ on purpose: notely.pipeline.assemble's
find_lecture_notes globs *.md in output/notes/, so anything written there
would get swept into the study guide -- see
test_essentials_output_is_not_swept_into_the_assembled_guide below.
"""

import json

from fakes import FakeLlmClient, llm_response

from notely.pipeline import assemble as m07
from notely.pipeline import essentials as m9
from notely.ports import LlmApiError


def _mkdirs(tmp_path, monkeypatch):
    notes_dir = tmp_path / "output" / "notes"
    essentials_dir = tmp_path / "output" / "essentials"
    guide_path = tmp_path / "output" / "study_guide.md"
    course_essentials_path = tmp_path / "output" / "essentials.md"
    raw_path = tmp_path / "output" / "essentials_raw.json"
    notes_dir.mkdir(parents=True)
    monkeypatch.setattr(m9, "INPUT_NOTES_DIR", notes_dir)
    monkeypatch.setattr(m9, "OUTPUT_ESSENTIALS_DIR", essentials_dir)
    monkeypatch.setattr(m9, "STUDY_GUIDE_PATH", guide_path)
    monkeypatch.setattr(m9, "COURSE_ESSENTIALS_PATH", course_essentials_path)
    monkeypatch.setattr(m9, "COURSE_ESSENTIALS_RAW_PATH", raw_path)
    return notes_dir, essentials_dir, guide_path, course_essentials_path, raw_path


# --- Stage 9: per-lecture essentials ----------------------------------------


def test_process_lecture_essentials_writes_output_and_sends_notes_text(tmp_path, monkeypatch):
    notes_dir, essentials_dir, *_ = _mkdirs(tmp_path, monkeypatch)
    (notes_dir / "lecture01.md").write_text(
        "# lecture01\n\n## Slide 1: Intro\n- Overview\n\n**Professor's notes:** Welcomed the class.\n",
        encoding="utf-8",
    )

    fake = FakeLlmClient(responses=[llm_response("## Must know\n- Overview of the topic.")])

    assert m9.process_lecture_essentials(fake, "lecture01", 1, 1, force=True) is True
    assert len(fake.calls) == 1

    # The notes text actually reached the prompt.
    sent = fake.calls[0]["messages"][0]["content"]
    assert "Overview" in sent
    assert "Welcomed the class" in sent

    output_path = essentials_dir / "lecture01.md"
    assert output_path.read_text(encoding="utf-8") == "## Must know\n- Overview of the topic."


def test_process_lecture_essentials_skip_without_force_is_a_noop(tmp_path, monkeypatch):
    notes_dir, essentials_dir, *_ = _mkdirs(tmp_path, monkeypatch)
    (notes_dir / "lecture01.md").write_text("# lecture01\n\nSome notes.\n", encoding="utf-8")
    essentials_dir.mkdir(parents=True)
    output_path = essentials_dir / "lecture01.md"
    output_path.write_text("already here", encoding="utf-8")
    mtime_before = output_path.stat().st_mtime_ns

    fake = FakeLlmClient(responses=[])  # calling past an empty list raises IndexError

    assert m9.process_lecture_essentials(fake, "lecture01", 1, 1, force=False) is True
    assert fake.calls == []
    assert output_path.stat().st_mtime_ns == mtime_before
    assert output_path.read_text(encoding="utf-8") == "already here"


def test_process_lecture_essentials_force_regenerates(tmp_path, monkeypatch):
    notes_dir, essentials_dir, *_ = _mkdirs(tmp_path, monkeypatch)
    (notes_dir / "lecture01.md").write_text("# lecture01\n\nSome notes.\n", encoding="utf-8")
    essentials_dir.mkdir(parents=True)
    (essentials_dir / "lecture01.md").write_text("stale", encoding="utf-8")

    fake = FakeLlmClient(responses=[llm_response("fresh essentials")])

    assert m9.process_lecture_essentials(fake, "lecture01", 1, 1, force=True) is True
    assert (essentials_dir / "lecture01.md").read_text(encoding="utf-8") == "fresh essentials"


def test_process_lecture_essentials_missing_input_returns_false(tmp_path, monkeypatch):
    _mkdirs(tmp_path, monkeypatch)
    fake = FakeLlmClient(responses=[])

    assert m9.process_lecture_essentials(fake, "lecture01", 1, 1, force=True) is False
    assert fake.calls == []  # never even tried -- there's nothing to summarize


def test_process_lecture_essentials_llm_error_returns_false_and_writes_raw_debug(tmp_path, monkeypatch):
    notes_dir, essentials_dir, *_ = _mkdirs(tmp_path, monkeypatch)
    (notes_dir / "lecture01.md").write_text("# lecture01\n\nSome notes.\n", encoding="utf-8")

    fake = FakeLlmClient(error=LlmApiError("rate limited"))

    assert m9.process_lecture_essentials(fake, "lecture01", 1, 1, force=True) is False
    assert not (essentials_dir / "lecture01.md").exists()

    raw = json.loads((essentials_dir / "lecture01_raw.json").read_text(encoding="utf-8"))
    assert raw["response"] is None
    assert raw["error"] == "rate limited"


def test_process_lecture_essentials_progress_line_matches_progress_contract(tmp_path, monkeypatch, capsys):
    notes_dir, *_ = _mkdirs(tmp_path, monkeypatch)
    (notes_dir / "lecture01.md").write_text("# lecture01\n\nSome notes.\n", encoding="utf-8")
    fake = FakeLlmClient(responses=[llm_response("ok")])

    m9.process_lecture_essentials(fake, "lecture01", 2, 5, force=True)

    out = capsys.readouterr().out
    assert "[2/5] lecture lecture01: ok" in out


# --- Stage 10: course-level essentials --------------------------------------


def test_process_course_essentials_aggregates_guide_and_lecture_sheets(tmp_path, monkeypatch):
    _, essentials_dir, guide_path, course_essentials_path, _ = _mkdirs(tmp_path, monkeypatch)
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("# lecture01\n\nFull study guide content.\n", encoding="utf-8")
    essentials_dir.mkdir(parents=True)
    (essentials_dir / "lecture01.md").write_text("## Must know\n- Key fact.", encoding="utf-8")

    fake = FakeLlmClient(responses=[llm_response("# Essentials\n- Key fact.")])

    assert m9.process_course_essentials(fake, force=True) is True
    sent = fake.calls[0]["messages"][0]["content"]
    assert "Full study guide content" in sent
    assert "Key fact" in sent
    assert course_essentials_path.read_text(encoding="utf-8") == "# Essentials\n- Key fact."


def test_process_course_essentials_works_without_any_lecture_sheets(tmp_path, monkeypatch):
    """Stage 9 is a separate, optional CLI step -- stage 10 must still run
    off the study guide alone if no one bothered running stage 9 first."""
    _, _, guide_path, course_essentials_path, _ = _mkdirs(tmp_path, monkeypatch)
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("# lecture01\n\nFull study guide content.\n", encoding="utf-8")

    fake = FakeLlmClient(responses=[llm_response("# Essentials")])

    assert m9.process_course_essentials(fake, force=True) is True
    assert course_essentials_path.exists()


def test_process_course_essentials_missing_guide_returns_false(tmp_path, monkeypatch):
    _mkdirs(tmp_path, monkeypatch)
    fake = FakeLlmClient(responses=[])

    assert m9.process_course_essentials(fake, force=True) is False
    assert fake.calls == []


def test_process_course_essentials_skip_without_force_is_a_noop(tmp_path, monkeypatch):
    _, _, guide_path, course_essentials_path, _ = _mkdirs(tmp_path, monkeypatch)
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("guide", encoding="utf-8")
    course_essentials_path.write_text("already assembled", encoding="utf-8")

    fake = FakeLlmClient(responses=[])
    assert m9.process_course_essentials(fake, force=False) is True
    assert fake.calls == []
    assert course_essentials_path.read_text(encoding="utf-8") == "already assembled"


def test_process_course_essentials_llm_error_returns_false_not_raise(tmp_path, monkeypatch):
    """Degrades like assemble.generate_topic_index (returns None on
    LlmApiError, never raises) -- but unlike the topic index, which is a
    bonus on top of assembly's real deliverable (the guide still
    assembles), essentials.md IS stage 10's only deliverable, so the
    *stage* reports failure (False) rather than silently "succeeding"
    with no output."""
    _, _, guide_path, course_essentials_path, raw_path = _mkdirs(tmp_path, monkeypatch)
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("guide", encoding="utf-8")

    fake = FakeLlmClient(error=LlmApiError("service unavailable"))

    assert m9.process_course_essentials(fake, force=True) is False
    assert not course_essentials_path.exists()
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw["response"] is None
    assert raw["error"] == "service unavailable"


# --- Regression guard: essentials must not pollute stage 7's *.md glob -----


def test_essentials_output_is_not_swept_into_the_assembled_guide(tmp_path, monkeypatch):
    notes_dir, essentials_dir, guide_path, course_essentials_path, _ = _mkdirs(tmp_path, monkeypatch)
    monkeypatch.setattr(m07, "get_project_root", lambda: tmp_path)

    (notes_dir / "lecture01.md").write_text("# lecture01\n\nNotes for lecture 1.\n", encoding="utf-8")
    (notes_dir / "lecture02.md").write_text("# lecture02\n\nNotes for lecture 2.\n", encoding="utf-8")

    assert m07.assemble_guide(force=True, topic_index=False) is True
    guide_before = guide_path.read_text(encoding="utf-8")

    fake9 = FakeLlmClient(
        responses=[llm_response("lecture01 essentials"), llm_response("lecture02 essentials")]
    )
    assert m9.process_lecture_essentials(fake9, "lecture01", 1, 2, force=True) is True
    assert m9.process_lecture_essentials(fake9, "lecture02", 2, 2, force=True) is True

    fake10 = FakeLlmClient(responses=[llm_response("course essentials")])
    assert m9.process_course_essentials(fake10, force=True) is True
    assert course_essentials_path.exists()

    # find_lecture_notes must still see exactly the two lecture notes --
    # essentials/lecture01.md and essentials.md must not have been swept in.
    note_files = m07.find_lecture_notes(tmp_path)
    assert [p.name for p in note_files] == ["lecture01.md", "lecture02.md"]

    # Re-assembling must be byte-identical to before essentials ran at all.
    assert m07.assemble_guide(force=True, topic_index=False) is True
    assert guide_path.read_text(encoding="utf-8") == guide_before
