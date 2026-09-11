"""webui/media.py's render_exam_pdf: the generated-exam -> PDF export
subprocess wrapper, same staleness check + atomic-rename shape as
render_guide_pdf (see test_media_guide_pdf.py) but parameterized over
which exam and paper-vs-key. subprocess.run is faked throughout -- no
real headless Chrome/08_export_pdf.py invocation."""

import os
import time

import pytest

from webui import config, media
from webui.errors import NotFoundError, ServerError


@pytest.fixture(autouse=True)
def _project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUT_EXAMS_DIR", tmp_path / "exams")
    monkeypatch.setattr(config, "SCRIPTS_DIR", tmp_path / "scripts")
    (tmp_path / "exams").mkdir()
    return tmp_path


def _fake_subprocess_run(write_pdf_bytes=b"%PDF-1.4 fake\n", returncode=0):
    def _run(cmd, **kwargs):
        out_path = cmd[cmd.index("--output") + 1]
        if returncode == 0 and write_pdf_bytes is not None:
            with open(out_path, "wb") as f:
                f.write(write_pdf_bytes)

        class _Result:
            pass

        r = _Result()
        r.returncode = returncode
        r.stdout = ""
        r.stderr = "" if returncode == 0 else "export failed"
        return r

    return _run


def test_raises_not_found_when_exam_does_not_exist(tmp_path):
    with pytest.raises(NotFoundError):
        media.render_exam_pdf("exam_01")


def test_regenerates_paper_when_no_pdf_exists_yet(tmp_path, monkeypatch):
    (config.OUTPUT_EXAMS_DIR / "exam_01.md").write_text("# Exam\n")
    monkeypatch.setattr(media.subprocess, "run", _fake_subprocess_run())

    pdf = media.render_exam_pdf("exam_01")

    assert pdf == config.OUTPUT_EXAMS_DIR / "exam_01.pdf"
    assert pdf.exists() and pdf.stat().st_size > 0


def test_requesting_key_reads_and_writes_the_key_variant(tmp_path, monkeypatch):
    (config.OUTPUT_EXAMS_DIR / "exam_01_key.md").write_text("# Key\n")
    calls = []
    monkeypatch.setattr(
        media.subprocess, "run", lambda cmd, **k: calls.append(cmd) or _fake_subprocess_run()(cmd, **k)
    )

    pdf = media.render_exam_pdf("exam_01", key=True)

    assert pdf == config.OUTPUT_EXAMS_DIR / "exam_01_key.pdf"
    assert "--exam" in calls[0] and calls[0][calls[0].index("--exam") + 1] == "exam_01"
    assert "--key" in calls[0]


def test_paper_request_does_not_pass_key_flag(tmp_path, monkeypatch):
    (config.OUTPUT_EXAMS_DIR / "exam_01.md").write_text("# Exam\n")
    calls = []
    monkeypatch.setattr(
        media.subprocess, "run", lambda cmd, **k: calls.append(cmd) or _fake_subprocess_run()(cmd, **k)
    )

    media.render_exam_pdf("exam_01", key=False)

    assert "--key" not in calls[0]


def test_skips_regeneration_when_pdf_is_already_fresh(tmp_path, monkeypatch):
    md = config.OUTPUT_EXAMS_DIR / "exam_01.md"
    md.write_text("# Exam\n")
    pdf = config.OUTPUT_EXAMS_DIR / "exam_01.pdf"
    pdf.write_bytes(b"%PDF-1.4 existing\n")
    os.utime(pdf, (time.time() + 10, time.time() + 10))

    calls = []
    monkeypatch.setattr(
        media.subprocess, "run", lambda *a, **k: calls.append(1) or _fake_subprocess_run()(*a, **k)
    )

    result = media.render_exam_pdf("exam_01")

    assert result == pdf
    assert calls == []
    assert pdf.read_bytes() == b"%PDF-1.4 existing\n"


def test_regenerates_when_pdf_is_older_than_the_markdown(tmp_path, monkeypatch):
    pdf = config.OUTPUT_EXAMS_DIR / "exam_01.pdf"
    pdf.write_bytes(b"%PDF-1.4 stale\n")
    os.utime(pdf, (time.time() - 10, time.time() - 10))
    md = config.OUTPUT_EXAMS_DIR / "exam_01.md"
    md.write_text("# Exam\n")

    monkeypatch.setattr(media.subprocess, "run", _fake_subprocess_run(write_pdf_bytes=b"%PDF-1.4 fresh\n"))

    result = media.render_exam_pdf("exam_01")

    assert result.read_bytes() == b"%PDF-1.4 fresh\n"


def test_raises_server_error_when_export_subprocess_fails(tmp_path, monkeypatch):
    (config.OUTPUT_EXAMS_DIR / "exam_01.md").write_text("# Exam\n")
    monkeypatch.setattr(media.subprocess, "run", _fake_subprocess_run(returncode=1, write_pdf_bytes=None))

    with pytest.raises(ServerError):
        media.render_exam_pdf("exam_01")

    assert list(config.OUTPUT_EXAMS_DIR.glob("*.pdf.tmp*")) == []


def test_key_and_paper_pdfs_are_independent(tmp_path, monkeypatch):
    """A stale/missing key PDF must not be satisfied by an existing paper
    PDF or vice versa -- they're different files."""
    (config.OUTPUT_EXAMS_DIR / "exam_01.md").write_text("# Exam\n")
    (config.OUTPUT_EXAMS_DIR / "exam_01_key.md").write_text("# Key\n")
    monkeypatch.setattr(media.subprocess, "run", _fake_subprocess_run())

    paper_pdf = media.render_exam_pdf("exam_01", key=False)
    key_pdf = media.render_exam_pdf("exam_01", key=True)

    assert paper_pdf != key_pdf
    assert paper_pdf.exists() and key_pdf.exists()
