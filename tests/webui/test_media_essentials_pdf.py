"""webui/media.py's render_essentials_pdf: course-level and per-lecture
essentials -> PDF export subprocess wrapper, same staleness check +
atomic-rename shape as render_exam_pdf/render_guide_pdf (see
test_media_exam_pdf.py) but parameterized over course vs one lecture's
sheet. subprocess.run is faked throughout -- no real headless
Chrome/08_export_pdf.py invocation."""

import os
import time

import pytest

from webui import config, media
from webui.errors import NotFoundError, ServerError


@pytest.fixture(autouse=True)
def _project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUT_ESSENTIALS_DIR", tmp_path / "essentials")
    monkeypatch.setattr(config, "COURSE_ESSENTIALS_PATH", tmp_path / "essentials.md")
    monkeypatch.setattr(config, "SCRIPTS_DIR", tmp_path / "scripts")
    (tmp_path / "essentials").mkdir()
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


# --- course-level (lecture_id=None) -----------------------------------------


def test_raises_not_found_when_course_essentials_does_not_exist():
    with pytest.raises(NotFoundError):
        media.render_essentials_pdf(None)


def test_course_regenerates_when_no_pdf_exists_yet(monkeypatch):
    config.COURSE_ESSENTIALS_PATH.write_text("# Essentials\n")
    monkeypatch.setattr(media.subprocess, "run", _fake_subprocess_run())

    pdf = media.render_essentials_pdf(None)

    assert pdf == config.OUTPUT_DIR / "essentials.pdf"
    assert pdf.exists() and pdf.stat().st_size > 0


def test_course_pdf_argv_passes_essentials_flag_without_lecture_id(monkeypatch):
    config.COURSE_ESSENTIALS_PATH.write_text("# Essentials\n")
    calls = []
    monkeypatch.setattr(
        media.subprocess, "run", lambda cmd, **k: calls.append(cmd) or _fake_subprocess_run()(cmd, **k)
    )

    media.render_essentials_pdf(None)

    assert "--essentials" in calls[0]


def test_course_skips_regeneration_when_pdf_is_already_fresh(monkeypatch):
    config.COURSE_ESSENTIALS_PATH.write_text("# Essentials\n")
    pdf = config.OUTPUT_DIR / "essentials.pdf"
    pdf.write_bytes(b"%PDF-1.4 existing\n")
    os.utime(pdf, (time.time() + 10, time.time() + 10))

    calls = []
    monkeypatch.setattr(
        media.subprocess, "run", lambda *a, **k: calls.append(1) or _fake_subprocess_run()(*a, **k)
    )

    result = media.render_essentials_pdf(None)

    assert result == pdf
    assert calls == []
    assert pdf.read_bytes() == b"%PDF-1.4 existing\n"


# --- per-lecture (lecture_id="lecture01") -----------------------------------


def test_raises_not_found_when_lecture_essentials_does_not_exist():
    with pytest.raises(NotFoundError):
        media.render_essentials_pdf("lecture01")


def test_lecture_regenerates_when_no_pdf_exists_yet(monkeypatch):
    (config.OUTPUT_ESSENTIALS_DIR / "lecture01.md").write_text("# Essentials\n")
    monkeypatch.setattr(media.subprocess, "run", _fake_subprocess_run())

    pdf = media.render_essentials_pdf("lecture01")

    assert pdf == config.OUTPUT_ESSENTIALS_DIR / "lecture01.pdf"
    assert pdf.exists() and pdf.stat().st_size > 0


def test_lecture_pdf_argv_passes_essentials_flag_and_lecture_id(monkeypatch):
    (config.OUTPUT_ESSENTIALS_DIR / "lecture01.md").write_text("# Essentials\n")
    calls = []
    monkeypatch.setattr(
        media.subprocess, "run", lambda cmd, **k: calls.append(cmd) or _fake_subprocess_run()(cmd, **k)
    )

    media.render_essentials_pdf("lecture01")

    assert "--essentials" in calls[0]
    assert "lecture01" in calls[0]


def test_course_and_lecture_pdfs_are_independent(monkeypatch):
    config.COURSE_ESSENTIALS_PATH.write_text("# Essentials\n")
    (config.OUTPUT_ESSENTIALS_DIR / "lecture01.md").write_text("# Essentials\n")
    monkeypatch.setattr(media.subprocess, "run", _fake_subprocess_run())

    course_pdf = media.render_essentials_pdf(None)
    lecture_pdf = media.render_essentials_pdf("lecture01")

    assert course_pdf != lecture_pdf
    assert course_pdf.exists() and lecture_pdf.exists()


def test_raises_server_error_when_export_subprocess_fails(monkeypatch):
    (config.OUTPUT_ESSENTIALS_DIR / "lecture01.md").write_text("# Essentials\n")
    monkeypatch.setattr(media.subprocess, "run", _fake_subprocess_run(returncode=1, write_pdf_bytes=None))

    with pytest.raises(ServerError):
        media.render_essentials_pdf("lecture01")

    assert list(config.OUTPUT_ESSENTIALS_DIR.glob("*.pdf.tmp*")) == []
