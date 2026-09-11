"""scripts/08_export_pdf.py's --exam/--key flags: PDF export for generated
practice exams (notely/pipeline/exams.py's stage 11) reuses the same
generic export_pdf(md_path, pdf_path) stage 8 already has -- same shape
as --essentials's own addition (see test_stage08_essentials_export.py).

export_pdf itself is monkeypatched to a recording stub in every test here
-- these tests pin path resolution, not headless-Chrome rendering."""

import sys

import pytest

from conftest import load_stage

s08 = load_stage("08_export_pdf.py")


def _stub_export_pdf(monkeypatch):
    calls = []
    monkeypatch.setattr(s08, "export_pdf", lambda md_path, pdf_path: calls.append((md_path, pdf_path)))
    return calls


def test_exam_flag_exports_the_paper(tmp_path, monkeypatch):
    calls = _stub_export_pdf(monkeypatch)
    monkeypatch.setattr(s08, "OUTPUT_DIR", tmp_path)
    (tmp_path / "exams").mkdir()
    (tmp_path / "exams" / "exam_01.md").write_text("# Exam", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["08_export_pdf.py", "--exam", "exam_01"])

    s08.main()

    assert calls == [(tmp_path / "exams" / "exam_01.md", tmp_path / "exams" / "exam_01.pdf")]


def test_exam_flag_with_key_exports_the_answer_key(tmp_path, monkeypatch):
    calls = _stub_export_pdf(monkeypatch)
    monkeypatch.setattr(s08, "OUTPUT_DIR", tmp_path)
    (tmp_path / "exams").mkdir()
    (tmp_path / "exams" / "exam_01_key.md").write_text("# Key", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["08_export_pdf.py", "--exam", "exam_01", "--key"])

    s08.main()

    assert calls == [(tmp_path / "exams" / "exam_01_key.md", tmp_path / "exams" / "exam_01_key.pdf")]


def test_exam_flag_respects_output_override(tmp_path, monkeypatch):
    calls = _stub_export_pdf(monkeypatch)
    monkeypatch.setattr(s08, "OUTPUT_DIR", tmp_path)
    (tmp_path / "exams").mkdir()
    (tmp_path / "exams" / "exam_01.md").write_text("# Exam", encoding="utf-8")
    custom_out = tmp_path / "custom.pdf"
    monkeypatch.setattr(sys, "argv", ["08_export_pdf.py", "--exam", "exam_01", "--output", str(custom_out)])

    s08.main()

    assert calls == [(tmp_path / "exams" / "exam_01.md", custom_out)]


def test_exam_flag_missing_source_exits_nonzero(tmp_path, monkeypatch):
    _stub_export_pdf(monkeypatch)
    monkeypatch.setattr(s08, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["08_export_pdf.py", "--exam", "exam_01"])

    with pytest.raises(SystemExit) as exc_info:
        s08.main()
    assert exc_info.value.code != 0


def test_key_flag_without_exam_flag_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(s08, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["08_export_pdf.py", "--key"])

    with pytest.raises(SystemExit) as exc_info:
        s08.main()
    assert exc_info.value.code == 2


def test_exam_and_essentials_together_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(s08, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["08_export_pdf.py", "--exam", "exam_01", "--essentials"])

    with pytest.raises(SystemExit) as exc_info:
        s08.main()
    assert exc_info.value.code == 2
