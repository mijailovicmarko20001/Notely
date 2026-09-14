"""scripts/08_export_pdf.py's --essentials flag: PDF export for the
essentials artifacts (notely/pipeline/essentials.py's stages 9/10) reuses
the same generic export_pdf(md_path, pdf_path) stage 8 already has for
the study guide and per-lecture notes -- see notely/pipeline/export.py's
own docstring ("or one lecture's notes"). This just teaches the CLI
wrapper the extra source, the same way --essentials was added to
run_pipeline.py.

export_pdf itself is monkeypatched to a recording stub in every test here
(same convention as test_run_pipeline_stage_flags.py's run_stage stub) --
these tests pin path resolution, not headless-Chrome rendering (already
covered by test_golden.py's stage 8 golden test)."""

import sys

import pytest

from conftest import load_stage

s08 = load_stage("08_export_pdf.py")


def _stub_export_pdf(monkeypatch):
    calls = []
    monkeypatch.setattr(s08, "export_pdf", lambda md_path, pdf_path: calls.append((md_path, pdf_path)))
    return calls


def test_essentials_flag_with_no_lecture_id_exports_course_essentials(tmp_path, monkeypatch):
    calls = _stub_export_pdf(monkeypatch)
    monkeypatch.setattr(s08, "OUTPUT_DIR", tmp_path)
    (tmp_path / "essentials.md").write_text("# Essentials", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["08_export_pdf.py", "--essentials"])

    s08.main()

    assert calls == [(tmp_path / "essentials.md", tmp_path / "essentials.pdf")]


def test_essentials_flag_with_lecture_id_exports_that_lectures_sheet(tmp_path, monkeypatch):
    calls = _stub_export_pdf(monkeypatch)
    monkeypatch.setattr(s08, "OUTPUT_DIR", tmp_path)
    (tmp_path / "essentials").mkdir()
    (tmp_path / "essentials" / "lecture01.md").write_text("## Must know", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["08_export_pdf.py", "lecture01", "--essentials"])

    s08.main()

    assert calls == [(tmp_path / "essentials" / "lecture01.md", tmp_path / "essentials" / "lecture01.pdf")]


def test_essentials_flag_respects_output_override(tmp_path, monkeypatch):
    calls = _stub_export_pdf(monkeypatch)
    monkeypatch.setattr(s08, "OUTPUT_DIR", tmp_path)
    (tmp_path / "essentials.md").write_text("# Essentials", encoding="utf-8")
    custom_out = tmp_path / "custom.pdf"
    monkeypatch.setattr(sys, "argv", ["08_export_pdf.py", "--essentials", "--output", str(custom_out)])

    s08.main()

    assert calls == [(tmp_path / "essentials.md", custom_out)]


def test_essentials_flag_missing_source_exits_nonzero(tmp_path, monkeypatch):
    _stub_export_pdf(monkeypatch)
    monkeypatch.setattr(s08, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["08_export_pdf.py", "--essentials"])

    with pytest.raises(SystemExit) as exc_info:
        s08.main()
    assert exc_info.value.code != 0


def test_without_essentials_flag_behavior_is_unchanged(tmp_path, monkeypatch):
    """Regression guard: adding --essentials must not disturb the existing
    guide/lecture-notes resolution."""
    calls = _stub_export_pdf(monkeypatch)
    monkeypatch.setattr(s08, "OUTPUT_DIR", tmp_path)
    (tmp_path / "study_guide.md").write_text("# Guide", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["08_export_pdf.py"])

    s08.main()

    assert calls == [(tmp_path / "study_guide.md", tmp_path / "study_guide.pdf")]
