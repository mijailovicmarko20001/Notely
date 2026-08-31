"""Contract test for notely.ports.HtmlToPdf (Phase 3, port 5 of 9).

Same shape as the DocConverter contract test: both FakeHtmlToPdf (golden
masters) and ChromeHtmlToPdf (the real adapter, its find_binary/
subprocess.run stubbed -- never a real Chrome invocation) must satisfy the
Protocol and share the same raise-on-failure contract."""

import subprocess

import pytest

from notely.adapters.chrome_html_to_pdf import ChromeHtmlToPdf
from notely.ports import HtmlToPdf, HtmlToPdfError
from tests.fakes import FakeHtmlToPdf


def test_both_implementations_satisfy_the_protocol():
    assert isinstance(FakeHtmlToPdf(), HtmlToPdf)
    assert isinstance(ChromeHtmlToPdf(), HtmlToPdf)


def test_fake_writes_a_real_pdf_file(tmp_path):
    fake = FakeHtmlToPdf(pdf_bytes=b"%PDF-1.4 fake\n%%EOF")
    pdf_path = tmp_path / "guide.pdf"

    fake.render("file:///tmp/guide.html", pdf_path)

    assert pdf_path.read_bytes() == b"%PDF-1.4 fake\n%%EOF"


def test_fake_records_every_call(tmp_path):
    fake = FakeHtmlToPdf()
    fake.render("file:///a.html", tmp_path / "a.pdf")
    fake.render("file:///b.html", tmp_path / "b.pdf")
    assert fake.calls == [
        ("file:///a.html", str(tmp_path / "a.pdf")),
        ("file:///b.html", str(tmp_path / "b.pdf")),
    ]


def test_fake_raises_configured_error_instead_of_rendering(tmp_path):
    fake = FakeHtmlToPdf(error=HtmlToPdfError("no chrome found"))
    with pytest.raises(HtmlToPdfError, match="no chrome found"):
        fake.render("file:///guide.html", tmp_path / "guide.pdf")


def test_real_adapter_raises_when_binary_not_found(tmp_path, monkeypatch):
    adapter = ChromeHtmlToPdf()
    monkeypatch.setattr(adapter, "find_binary", lambda: None)

    with pytest.raises(HtmlToPdfError, match="no Chrome"):
        adapter.render("file:///guide.html", tmp_path / "guide.pdf")


def test_real_adapter_raises_when_subprocess_returns_nonzero(tmp_path, monkeypatch):
    adapter = ChromeHtmlToPdf()
    monkeypatch.setattr(adapter, "find_binary", lambda: "/usr/bin/chromium")

    class _Result:
        returncode = 1
        stderr = "chrome crashed"

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: _Result())

    with pytest.raises(HtmlToPdfError, match="chrome print failed"):
        adapter.render("file:///guide.html", tmp_path / "guide.pdf")


def test_real_adapter_raises_when_pdf_file_is_missing_despite_zero_exit(tmp_path, monkeypatch):
    adapter = ChromeHtmlToPdf()
    monkeypatch.setattr(adapter, "find_binary", lambda: "/usr/bin/chromium")

    class _Result:
        returncode = 0
        stderr = ""

    # exits 0 but never actually wrote the file
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: _Result())

    with pytest.raises(HtmlToPdfError):
        adapter.render("file:///guide.html", tmp_path / "guide.pdf")


def test_real_adapter_raises_on_timeout(tmp_path, monkeypatch):
    adapter = ChromeHtmlToPdf()
    monkeypatch.setattr(adapter, "find_binary", lambda: "/usr/bin/chromium")

    def _raise(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd="chromium", timeout=180)

    monkeypatch.setattr(subprocess, "run", _raise)

    with pytest.raises(HtmlToPdfError):
        adapter.render("file:///guide.html", tmp_path / "guide.pdf")


def test_real_adapter_succeeds_when_subprocess_writes_the_file(tmp_path, monkeypatch):
    adapter = ChromeHtmlToPdf()
    monkeypatch.setattr(adapter, "find_binary", lambda: "/usr/bin/chromium")
    pdf_path = tmp_path / "guide.pdf"

    class _Result:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **kwargs):
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
        return _Result()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    adapter.render("file:///guide.html", pdf_path)  # must not raise

    assert pdf_path.exists()
