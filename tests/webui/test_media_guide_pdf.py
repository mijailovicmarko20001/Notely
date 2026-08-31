"""Characterization tests for webui/media.py's render_guide_pdf (previously
zero coverage): the study-guide -> PDF export subprocess wrapper, its
staleness check, and the atomic-rename write. subprocess.run is faked
throughout -- no real headless Chrome/08_export_pdf.py invocation."""

import os
import time

import pytest

from webui import config, media
from webui.errors import NotFoundError, ServerError


@pytest.fixture(autouse=True)
def _project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "SCRIPTS_DIR", tmp_path / "scripts")
    return tmp_path


def _fake_subprocess_run(write_pdf_bytes=b"%PDF-1.4 fake\n", returncode=0):
    """A stand-in for subprocess.run(["python", "08_export_pdf.py", "--output", <tmp>]):
    writes to whatever path follows --output, the same contract the real
    script honors."""

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


def test_raises_not_found_when_no_study_guide_yet(tmp_path):
    with pytest.raises(NotFoundError):
        media.render_guide_pdf()


def test_regenerates_when_no_pdf_exists_yet(tmp_path, monkeypatch):
    (tmp_path / "study_guide.md").write_text("# Guide\n")
    monkeypatch.setattr(media.subprocess, "run", _fake_subprocess_run())

    pdf = media.render_guide_pdf()

    assert pdf == tmp_path / "study_guide.pdf"
    assert pdf.exists() and pdf.stat().st_size > 0
    # no leftover tmp file from the atomic write
    assert list(tmp_path.glob("*.pdf.tmp*")) == []


def test_skips_regeneration_when_pdf_is_already_fresh(tmp_path, monkeypatch):
    md = tmp_path / "study_guide.md"
    md.write_text("# Guide\n")
    pdf = tmp_path / "study_guide.pdf"
    pdf.write_bytes(b"%PDF-1.4 existing\n")
    # make sure the PDF's mtime is unambiguously after the md's
    os.utime(pdf, (time.time() + 10, time.time() + 10))

    calls = []
    monkeypatch.setattr(
        media.subprocess, "run", lambda *a, **k: calls.append(1) or _fake_subprocess_run()(*a, **k)
    )

    result = media.render_guide_pdf()

    assert result == pdf
    assert calls == []  # never shelled out -- the existing PDF was trusted
    assert pdf.read_bytes() == b"%PDF-1.4 existing\n"


def test_regenerates_when_pdf_is_older_than_the_markdown(tmp_path, monkeypatch):
    pdf = tmp_path / "study_guide.pdf"
    pdf.write_bytes(b"%PDF-1.4 stale\n")
    os.utime(pdf, (time.time() - 10, time.time() - 10))
    md = tmp_path / "study_guide.md"
    md.write_text("# Guide\n")  # newer than the pdf

    monkeypatch.setattr(media.subprocess, "run", _fake_subprocess_run(write_pdf_bytes=b"%PDF-1.4 fresh\n"))

    result = media.render_guide_pdf()

    assert result.read_bytes() == b"%PDF-1.4 fresh\n"


def test_raises_server_error_when_export_subprocess_fails(tmp_path, monkeypatch):
    (tmp_path / "study_guide.md").write_text("# Guide\n")
    monkeypatch.setattr(media.subprocess, "run", _fake_subprocess_run(returncode=1, write_pdf_bytes=None))

    with pytest.raises(ServerError):
        media.render_guide_pdf()

    # the failed attempt's tmp file must not linger
    assert list(tmp_path.glob("*.pdf.tmp*")) == []


def test_raises_server_error_when_subprocess_reports_success_but_writes_nothing(tmp_path, monkeypatch):
    (tmp_path / "study_guide.md").write_text("# Guide\n")
    monkeypatch.setattr(media.subprocess, "run", _fake_subprocess_run(returncode=0, write_pdf_bytes=None))

    with pytest.raises(ServerError):
        media.render_guide_pdf()
