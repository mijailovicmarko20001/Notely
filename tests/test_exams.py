"""Stage [11]: practice exam generation -- written before
notely.pipeline.exams exists (test-first, per the approved plan).

Two phases:
- Format analysis (generate_exam_format / load_format_blueprint): one
  Claude call over uploaded past-exam pages (input/exams/*.pdf) --
  optional; format analysis still runs off the course material alone if
  none were uploaded -- producing a JSON blueprint (structure, question
  types, counts, points, duration). Cached to output/exams/_format.json
  so tweaking the paper prompt doesn't re-pay for it.
- Paper generation (generate_one_exam / process_exam_generation): one
  call per requested paper, blueprint + course material in, a single
  response containing both the student-facing paper and a separate
  answer key (split on ANSWER_KEY_MARKER), written to exam_NN.md /
  exam_NN_key.md.

The uploaded exams are a FORMAT template only -- the format-analysis
prompt is the enforceable half of "don't copy their questions" (assert on
what reaches which prompt); the semantic half (whether the model actually
obeys) is a manual read-through, not something a fake client can check.
"""

import json

from fakes import FakeLlmClient, llm_response
from pdf_fixtures import make_pdf_bytes

from notely.pipeline import exams as m11
from notely.ports import LlmApiError

# --- extraction ---------------------------------------------------------


_LONG_Q1 = "Question 1 What is 2 plus 2 explain your reasoning in full detail please"
_LONG_Q2 = "Question 2 Name the capital city of France and briefly describe its history"


def test_extract_exam_pages_reads_real_text(tmp_path):
    pdf_path = tmp_path / "exam.pdf"
    pdf_path.write_bytes(make_pdf_bytes([_LONG_Q1, _LONG_Q2]))
    pages = m11.extract_exam_pages(pdf_path, tmp_path / "images")

    assert [p["page_number"] for p in pages] == [1, 2]
    assert pages[0]["text"] == _LONG_Q1
    assert pages[0]["image_path"] is None
    assert pages[1]["text"] == _LONG_Q2


def test_extract_exam_pages_renders_scanned_page_as_image(tmp_path):
    pdf_path = tmp_path / "exam.pdf"
    pdf_path.write_bytes(make_pdf_bytes(["", _LONG_Q2]))
    image_dir = tmp_path / "images"
    pages = m11.extract_exam_pages(pdf_path, image_dir, scanned_text_threshold=50)

    assert pages[0]["text"] == ""
    assert pages[0]["image_path"] is not None
    rendered = list(image_dir.glob("*.png"))
    assert len(rendered) == 1
    assert rendered[0].stat().st_size > 0


def test_extract_exam_pages_only_renders_scanned_pages(tmp_path):
    pdf_path = tmp_path / "exam.pdf"
    pdf_path.write_bytes(make_pdf_bytes(["Real text here plenty of it to pass threshold easily", ""]))
    image_dir = tmp_path / "images"
    pages = m11.extract_exam_pages(pdf_path, image_dir, scanned_text_threshold=10)

    assert pages[0]["image_path"] is None
    assert pages[1]["image_path"] is not None
    rendered = list(image_dir.glob("*.png"))
    assert len(rendered) == 1  # only the scanned page was rendered


def test_collect_uploaded_exam_pages_returns_empty_list_for_missing_dir(tmp_path):
    assert m11.collect_uploaded_exam_pages(tmp_path / "does_not_exist", tmp_path / "images") == []


def test_collect_uploaded_exam_pages_tags_each_page_with_its_source_file(tmp_path):
    input_dir = tmp_path / "exams"
    input_dir.mkdir()
    (input_dir / "past2023.pdf").write_bytes(make_pdf_bytes(["Q1 text here"]))
    (input_dir / "past2024.pdf").write_bytes(make_pdf_bytes(["Q1 different text"]))

    pages = m11.collect_uploaded_exam_pages(input_dir, tmp_path / "images")

    sources = sorted(p["source"] for p in pages)
    assert sources == ["past2023.pdf", "past2024.pdf"]


# --- format analysis -----------------------------------------------------


def test_generate_exam_format_sends_uploaded_exam_text_and_parses_json_blueprint(tmp_path):
    pages = [{"source": "past2023.pdf", "page_number": 1, "text": "Question 1: derive X", "image_path": None}]
    blueprint = {"total_questions": 5, "sections": [], "duration_minutes": 90}
    fake = FakeLlmClient(responses=[llm_response(json.dumps(blueprint))])

    result = m11.generate_exam_format(
        fake, pages, "course material excerpt", "claude-sonnet-5", tmp_path / "raw.json"
    )

    assert result == blueprint
    sent = fake.calls[0]["messages"][0]["content"]
    sent_text = sent if isinstance(sent, str) else json.dumps(sent)
    assert "derive X" in sent_text
    assert "course material excerpt" in sent_text


def test_generate_exam_format_works_with_no_uploaded_pages(tmp_path):
    """Optional input -- format analysis must still produce a sensible
    blueprint from the course material alone."""
    blueprint = {"total_questions": 8, "sections": [], "duration_minutes": None}
    fake = FakeLlmClient(responses=[llm_response(json.dumps(blueprint))])

    result = m11.generate_exam_format(
        fake, [], "course material excerpt", "claude-sonnet-5", tmp_path / "raw.json"
    )

    assert result == blueprint
    assert len(fake.calls) == 1


def test_generate_exam_format_returns_none_on_unparseable_response(tmp_path):
    fake = FakeLlmClient(responses=[llm_response("not json at all")])
    result = m11.generate_exam_format(fake, [], "material", "claude-sonnet-5", tmp_path / "raw.json")
    assert result is None


def test_generate_exam_format_tolerates_a_fenced_code_block(tmp_path):
    blueprint = {"total_questions": 3, "sections": [], "duration_minutes": 60}
    fenced = f"```json\n{json.dumps(blueprint)}\n```"
    fake = FakeLlmClient(responses=[llm_response(fenced)])
    result = m11.generate_exam_format(fake, [], "material", "claude-sonnet-5", tmp_path / "raw.json")
    assert result == blueprint


def test_generate_exam_format_returns_none_on_llm_error(tmp_path):
    fake = FakeLlmClient(error=LlmApiError("rate limited"))
    raw_path = tmp_path / "raw.json"
    result = m11.generate_exam_format(fake, [], "material", "claude-sonnet-5", raw_path)
    assert result is None
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw["response"] is None
    assert raw["error"] == "rate limited"


# --- format blueprint caching --------------------------------------------


def _mkdirs(tmp_path, monkeypatch):
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
    return input_exams_dir, output_exams_dir, guide_path, course_essentials_path


def test_load_format_blueprint_writes_and_reuses_cache(tmp_path, monkeypatch):
    input_exams_dir, output_exams_dir, *_ = _mkdirs(tmp_path, monkeypatch)
    blueprint = {"total_questions": 4, "sections": [], "duration_minutes": 45}
    fake = FakeLlmClient(responses=[llm_response(json.dumps(blueprint))])

    result1 = m11.load_format_blueprint(fake, "course material", force=False)
    assert result1 == blueprint
    assert len(fake.calls) == 1
    assert (output_exams_dir / "_format.json").exists()

    # Second call, no --force: must NOT call the LLM again.
    fake2 = FakeLlmClient(responses=[])
    result2 = m11.load_format_blueprint(fake2, "course material", force=False)
    assert result2 == blueprint
    assert fake2.calls == []


def test_load_format_blueprint_force_regenerates(tmp_path, monkeypatch):
    _mkdirs(tmp_path, monkeypatch)
    old_blueprint = {"total_questions": 4, "sections": [], "duration_minutes": 45}
    new_blueprint = {"total_questions": 9, "sections": [], "duration_minutes": 120}
    fake1 = FakeLlmClient(responses=[llm_response(json.dumps(old_blueprint))])
    m11.load_format_blueprint(fake1, "course material", force=False)

    fake2 = FakeLlmClient(responses=[llm_response(json.dumps(new_blueprint))])
    result = m11.load_format_blueprint(fake2, "course material", force=True)
    assert result == new_blueprint
    assert len(fake2.calls) == 1


def test_load_format_blueprint_picks_up_uploaded_exam_pdfs(tmp_path, monkeypatch):
    input_exams_dir, *_ = _mkdirs(tmp_path, monkeypatch)
    input_exams_dir.mkdir(parents=True)
    (input_exams_dir / "past.pdf").write_bytes(make_pdf_bytes(["Section A worth 10 points each"]))
    blueprint = {"total_questions": 5, "sections": [], "duration_minutes": 90}
    fake = FakeLlmClient(responses=[llm_response(json.dumps(blueprint))])

    m11.load_format_blueprint(fake, "course material", force=False)

    sent = fake.calls[0]["messages"][0]["content"]
    sent_text = sent if isinstance(sent, str) else json.dumps(sent)
    assert "Section A worth 10 points" in sent_text


# --- paper + key generation -----------------------------------------------


def test_split_exam_and_key_splits_on_marker():
    text = f"# Exam\n\nQ1...{m11.ANSWER_KEY_MARKER}# Answer Key\n\nA1..."
    paper, key = m11.split_exam_and_key(text)
    assert paper.strip() == "# Exam\n\nQ1..."
    assert key.strip() == "# Answer Key\n\nA1..."


def test_split_exam_and_key_missing_marker_returns_none_key():
    paper, key = m11.split_exam_and_key("# Exam\n\nQ1... (no marker)")
    assert paper == "# Exam\n\nQ1... (no marker)"
    assert key is None


def test_generate_one_exam_writes_paper_and_key_never_raises_on_success(tmp_path):
    blueprint = {"total_questions": 2, "sections": [], "duration_minutes": 30}
    text = f"# Exam\n\nQ1: ...{m11.ANSWER_KEY_MARKER}# Key\n\nA1: ..."
    fake = FakeLlmClient(responses=[llm_response(text)])

    result = m11.generate_one_exam(
        fake, blueprint, "course material", "claude-sonnet-5", 1, 1, tmp_path / "raw.json"
    )

    assert result["error"] is False
    assert result["paper_text"].strip() == "# Exam\n\nQ1: ..."
    assert result["key_text"].strip() == "# Key\n\nA1: ..."
    sent = fake.calls[0]["messages"][0]["content"]
    assert "course material" in sent
    assert json.dumps(blueprint)[:20] in sent or "total_questions" in sent


def test_generate_one_exam_error_never_raises(tmp_path):
    fake = FakeLlmClient(error=LlmApiError("service down"))
    result = m11.generate_one_exam(
        fake, {"total_questions": 1}, "material", "claude-sonnet-5", 1, 1, tmp_path / "r.json"
    )
    assert result["error"] is True
    assert result["paper_text"] is None


# --- full orchestration: process_exam_generation --------------------------


def test_process_exam_generation_writes_numbered_papers(tmp_path, monkeypatch):
    _, output_exams_dir, guide_path, _ = _mkdirs(tmp_path, monkeypatch)
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("# lecture01\n\nCourse content.\n", encoding="utf-8")

    blueprint = {"total_questions": 2, "sections": [], "duration_minutes": 30}
    text1 = f"# Exam 1{m11.ANSWER_KEY_MARKER}# Key 1"
    text2 = f"# Exam 2{m11.ANSWER_KEY_MARKER}# Key 2"
    fake = FakeLlmClient(
        responses=[llm_response(json.dumps(blueprint)), llm_response(text1), llm_response(text2)]
    )

    assert m11.process_exam_generation(fake, count=2, force=True) is True

    assert (output_exams_dir / "exam_01.md").read_text(encoding="utf-8").strip() == "# Exam 1"
    assert (output_exams_dir / "exam_01_key.md").read_text(encoding="utf-8").strip() == "# Key 1"
    assert (output_exams_dir / "exam_02.md").read_text(encoding="utf-8").strip() == "# Exam 2"
    assert (output_exams_dir / "exam_02_key.md").read_text(encoding="utf-8").strip() == "# Key 2"


def test_process_exam_generation_prefers_essentials_over_study_guide(tmp_path, monkeypatch):
    _, _, guide_path, course_essentials_path = _mkdirs(tmp_path, monkeypatch)
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("# guide (should not be used)\n", encoding="utf-8")
    course_essentials_path.write_text("# essentials (should be used)\n", encoding="utf-8")

    blueprint = {"total_questions": 1, "sections": [], "duration_minutes": 30}
    text = f"# Exam{m11.ANSWER_KEY_MARKER}# Key"
    fake = FakeLlmClient(responses=[llm_response(json.dumps(blueprint)), llm_response(text)])

    assert m11.process_exam_generation(fake, count=1, force=True) is True

    format_prompt = fake.calls[0]["messages"][0]["content"]
    format_prompt_text = format_prompt if isinstance(format_prompt, str) else json.dumps(format_prompt)
    assert "should be used" in format_prompt_text
    assert "should not be used" not in format_prompt_text


def test_process_exam_generation_missing_course_material_returns_false(tmp_path, monkeypatch):
    _mkdirs(tmp_path, monkeypatch)
    fake = FakeLlmClient(responses=[])
    assert m11.process_exam_generation(fake, count=1, force=True) is False
    assert fake.calls == []


def test_process_exam_generation_skips_existing_papers_without_force(tmp_path, monkeypatch):
    _, output_exams_dir, guide_path, _ = _mkdirs(tmp_path, monkeypatch)
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("# guide\n", encoding="utf-8")
    output_exams_dir.mkdir(parents=True)
    (output_exams_dir / "exam_01.md").write_text("already here", encoding="utf-8")
    (output_exams_dir / "exam_01_key.md").write_text("already here key", encoding="utf-8")
    (output_exams_dir / "_format.json").write_text(
        json.dumps(
            {
                "model": "claude-sonnet-5",
                "blueprint": {"total_questions": 1, "sections": [], "duration_minutes": 30},
            }
        ),
        encoding="utf-8",
    )

    fake = FakeLlmClient(responses=[])  # must not be called at all
    assert m11.process_exam_generation(fake, count=1, force=False) is True
    assert fake.calls == []
    assert (output_exams_dir / "exam_01.md").read_text(encoding="utf-8") == "already here"


def test_process_exam_generation_no_uploaded_exams_still_succeeds(tmp_path, monkeypatch):
    """The optional-input path: format analysis + generation both work off
    the course material alone when input/exams/ has nothing in it."""
    _, output_exams_dir, guide_path, _ = _mkdirs(tmp_path, monkeypatch)
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("# guide\n\nSome material.\n", encoding="utf-8")

    blueprint = {"total_questions": 1, "sections": [], "duration_minutes": 30}
    text = f"# Exam{m11.ANSWER_KEY_MARKER}# Key"
    fake = FakeLlmClient(responses=[llm_response(json.dumps(blueprint)), llm_response(text)])

    assert m11.process_exam_generation(fake, count=1, force=True) is True
    assert (output_exams_dir / "exam_01.md").exists()


def test_process_exam_generation_returns_false_if_format_analysis_fails(tmp_path, monkeypatch):
    _, _, guide_path, _ = _mkdirs(tmp_path, monkeypatch)
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("# guide\n", encoding="utf-8")

    fake = FakeLlmClient(error=LlmApiError("down"))
    assert m11.process_exam_generation(fake, count=1, force=True) is False


def test_process_exam_generation_returns_false_if_any_paper_fails(tmp_path, monkeypatch):
    _, output_exams_dir, guide_path, _ = _mkdirs(tmp_path, monkeypatch)
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("# guide\n", encoding="utf-8")

    blueprint = {"total_questions": 1, "sections": [], "duration_minutes": 30}

    # FakeLlmClient only supports "all calls succeed" or "all calls fail" --
    # this needs the first call (format analysis) to succeed and the second
    # (paper generation) to fail, so a small local stand-in fills in.
    class _FlakyClient:
        def __init__(self, first_response):
            self.calls = []
            self._first = first_response
            self._n = 0

        def create_message(self, **payload):
            self.calls.append(payload)
            self._n += 1
            if self._n == 1:
                return self._first
            raise LlmApiError("paper generation failed")

    flaky = _FlakyClient(llm_response(json.dumps(blueprint)))
    assert m11.process_exam_generation(flaky, count=1, force=True) is False
    assert not (output_exams_dir / "exam_01.md").exists()


def test_process_exam_generation_questions_override_reaches_blueprint(tmp_path, monkeypatch):
    _, output_exams_dir, guide_path, _ = _mkdirs(tmp_path, monkeypatch)
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text("# guide\n", encoding="utf-8")

    blueprint = {"total_questions": 5, "sections": [], "duration_minutes": 90}
    text = f"# Exam{m11.ANSWER_KEY_MARKER}# Key"
    fake = FakeLlmClient(responses=[llm_response(json.dumps(blueprint)), llm_response(text)])

    assert m11.process_exam_generation(fake, count=1, force=True, questions_override=12) is True

    paper_prompt = fake.calls[1]["messages"][0]["content"]
    assert '"total_questions": 12' in paper_prompt

    cached = json.loads((output_exams_dir / "_format.json").read_text(encoding="utf-8"))
    # The override must not corrupt the cache itself -- only the prompt
    # actually sent for this run.
    assert cached["blueprint"]["total_questions"] == 5
