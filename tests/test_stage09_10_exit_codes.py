"""Stage 9/10 CLI wrapper conventions -- same contract every other stage's
main() follows (see test_stage_exit_codes.py, test_lecture_id_all_mutually_
exclusive.py): missing required input exits nonzero, an already-done
unforced skip exits zero, and <lecture_id> + --all together is rejected."""

import sys

import pytest
from fakes import FakeLlmClient, llm_response

from conftest import load_stage
from notely.pipeline import essentials as m9

s09 = load_stage("09_lecture_essentials.py")
s10 = load_stage("10_course_essentials.py")


@pytest.fixture()
def project(tmp_path, monkeypatch):
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
    # scripts/09_lecture_essentials.py's own main() reads its wrapper-level
    # re-exported INPUT_NOTES_DIR name directly (`from notely.pipeline.
    # essentials import INPUT_NOTES_DIR`, same convention as
    # scripts/02_extract_slides.py's SLIDES_DIR) for its --all glob -- that
    # binding is separate from notely.pipeline.essentials's own module
    # attribute patched above, so it needs its own patch too (same
    # requirement test_lecture_id_all_mutually_exclusive.py's
    # s02.SLIDES_DIR patch satisfies for stage 2's wrapper).
    monkeypatch.setattr(s09, "INPUT_NOTES_DIR", notes_dir)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(s09, "load_dotenv_if_available", lambda: None)
    monkeypatch.setattr(s10, "load_dotenv_if_available", lambda: None)
    return tmp_path, notes_dir, essentials_dir, guide_path, course_essentials_path


def test_stage09_main_rejects_lecture_id_and_all_together(project, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["09_lecture_essentials.py", "lecture01", "--all"])
    with pytest.raises(SystemExit) as exc_info:
        s09.main()
    assert exc_info.value.code == 2


def test_stage09_main_exits_nonzero_for_missing_notes(project, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["09_lecture_essentials.py", "lecture01"])
    with pytest.raises(SystemExit) as exc_info:
        s09.main()
    assert exc_info.value.code != 0


def test_stage09_main_exits_zero_when_already_done_and_not_forced(project, monkeypatch):
    _, notes_dir, essentials_dir, *_ = project
    (notes_dir / "lecture01.md").write_text("# lecture01\n\nnotes\n", encoding="utf-8")
    essentials_dir.mkdir(parents=True)
    (essentials_dir / "lecture01.md").write_text("already here", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["09_lecture_essentials.py", "lecture01"])
    # must not raise SystemExit at all -- a clean, unforced skip is success
    s09.main()


def test_stage09_main_requires_api_key(project, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["09_lecture_essentials.py", "lecture01"])
    with pytest.raises(SystemExit) as exc_info:
        s09.main()
    assert exc_info.value.code == 1


def test_stage09_main_all_with_no_notes_found_does_not_exit_nonzero(project, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["09_lecture_essentials.py", "--all"])
    s09.main()  # nothing to do -- not a failure


def test_stage10_main_exits_nonzero_for_missing_guide(project, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["10_course_essentials.py"])
    with pytest.raises(SystemExit) as exc_info:
        s10.main()
    assert exc_info.value.code != 0


def test_stage10_main_exits_zero_when_already_done_and_not_forced(project, monkeypatch):
    _, _, _, guide_path, course_essentials_path = project
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("guide", encoding="utf-8")
    course_essentials_path.write_text("already assembled", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["10_course_essentials.py"])
    s10.main()  # must not raise


def test_stage10_main_requires_api_key(project, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["10_course_essentials.py"])
    with pytest.raises(SystemExit) as exc_info:
        s10.main()
    assert exc_info.value.code == 1


def test_stage09_main_all_processes_every_lecture_note(project, monkeypatch):
    _, notes_dir, essentials_dir, *_ = project
    (notes_dir / "lecture01.md").write_text("notes 1", encoding="utf-8")
    (notes_dir / "lecture02.md").write_text("notes 2", encoding="utf-8")

    fake = FakeLlmClient(responses=[llm_response("sheet 1"), llm_response("sheet 2")])
    monkeypatch.setattr("notely.adapters.anthropic_llm.AnthropicLlmClient", lambda **kwargs: fake)
    monkeypatch.setattr(sys, "argv", ["09_lecture_essentials.py", "--all", "--force"])
    s09.main()

    assert (essentials_dir / "lecture01.md").read_text(encoding="utf-8") == "sheet 1"
    assert (essentials_dir / "lecture02.md").read_text(encoding="utf-8") == "sheet 2"
