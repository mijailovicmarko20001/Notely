"""Stage 11 CLI wrapper conventions -- same contract as every other
stage's main() (see test_stage_exit_codes.py, test_stage09_10_exit_codes.py):
missing required input exits nonzero, an already-done unforced skip exits
zero, and flags reach process_exam_generation correctly."""

import sys

import pytest
from fakes import FakeLlmClient, llm_response

from conftest import load_stage
from notely.pipeline import exams as m11

s11 = load_stage("11_generate_exam.py")


@pytest.fixture()
def project(tmp_path, monkeypatch):
    input_exams_dir = tmp_path / "input" / "exams"
    output_exams_dir = tmp_path / "output" / "exams"
    guide_path = tmp_path / "output" / "study_guide.md"
    course_essentials_path = tmp_path / "output" / "essentials.md"
    monkeypatch.setattr(m11, "INPUT_EXAMS_DIR", input_exams_dir)
    monkeypatch.setattr(m11, "OUTPUT_EXAMS_DIR", output_exams_dir)
    monkeypatch.setattr(m11, "STUDY_GUIDE_PATH", guide_path)
    monkeypatch.setattr(m11, "COURSE_ESSENTIALS_PATH", course_essentials_path)
    monkeypatch.setattr(m11, "FORMAT_CACHE_PATH", output_exams_dir / "_format.json")
    monkeypatch.setattr(m11, "EXAM_IMAGE_DIR", output_exams_dir / "_source_images")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(s11, "load_dotenv_if_available", lambda: None)
    return tmp_path, guide_path, output_exams_dir


def test_stage11_main_exits_nonzero_for_missing_course_material(project, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["11_generate_exam.py"])
    with pytest.raises(SystemExit) as exc_info:
        s11.main()
    assert exc_info.value.code != 0


def test_stage11_main_requires_api_key(project, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["11_generate_exam.py"])
    with pytest.raises(SystemExit) as exc_info:
        s11.main()
    assert exc_info.value.code == 1


def test_stage11_main_exits_zero_when_already_done_and_not_forced(project, monkeypatch):
    _, guide_path, output_exams_dir = project
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("guide", encoding="utf-8")
    output_exams_dir.mkdir(parents=True)
    (output_exams_dir / "exam_01.md").write_text("already here", encoding="utf-8")
    (output_exams_dir / "exam_01_key.md").write_text("already here key", encoding="utf-8")
    (output_exams_dir / "_format.json").write_text(
        '{"model": "claude-sonnet-5", "blueprint": {"total_questions": 1}}', encoding="utf-8"
    )
    monkeypatch.setattr(sys, "argv", ["11_generate_exam.py"])
    s11.main()  # must not raise


def test_stage11_main_default_count_is_one(project, monkeypatch):
    _, guide_path, output_exams_dir = project
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("guide", encoding="utf-8")

    blueprint = {"total_questions": 1}
    import json

    text = f"# Exam{m11.ANSWER_KEY_MARKER}# Key"
    fake = FakeLlmClient(responses=[llm_response(json.dumps(blueprint)), llm_response(text)])
    monkeypatch.setattr("notely.adapters.anthropic_llm.AnthropicLlmClient", lambda **kwargs: fake)
    monkeypatch.setattr(sys, "argv", ["11_generate_exam.py", "--force"])

    s11.main()

    assert (output_exams_dir / "exam_01.md").exists()
    assert not (output_exams_dir / "exam_02.md").exists()


def test_stage11_main_count_flag_generates_multiple_papers(project, monkeypatch):
    _, guide_path, output_exams_dir = project
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("guide", encoding="utf-8")

    import json

    blueprint = {"total_questions": 1}
    text1 = f"# Exam 1{m11.ANSWER_KEY_MARKER}# Key 1"
    text2 = f"# Exam 2{m11.ANSWER_KEY_MARKER}# Key 2"
    fake = FakeLlmClient(
        responses=[llm_response(json.dumps(blueprint)), llm_response(text1), llm_response(text2)]
    )
    monkeypatch.setattr("notely.adapters.anthropic_llm.AnthropicLlmClient", lambda **kwargs: fake)
    monkeypatch.setattr(sys, "argv", ["11_generate_exam.py", "--count", "2", "--force"])

    s11.main()

    assert (output_exams_dir / "exam_01.md").exists()
    assert (output_exams_dir / "exam_02.md").exists()


def test_stage11_main_questions_flag_overrides_blueprint(project, monkeypatch):
    _, guide_path, output_exams_dir = project
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("guide", encoding="utf-8")

    import json

    blueprint = {"total_questions": 5}
    text = f"# Exam{m11.ANSWER_KEY_MARKER}# Key"
    fake = FakeLlmClient(responses=[llm_response(json.dumps(blueprint)), llm_response(text)])
    monkeypatch.setattr("notely.adapters.anthropic_llm.AnthropicLlmClient", lambda **kwargs: fake)
    monkeypatch.setattr(sys, "argv", ["11_generate_exam.py", "--questions", "20", "--force"])

    s11.main()

    paper_prompt = fake.calls[1]["messages"][0]["content"]
    assert '"total_questions": 20' in paper_prompt
