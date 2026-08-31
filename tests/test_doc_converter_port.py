"""Contract test for notely.ports.DocConverter (Phase 3, port 4 of 9).

Same shape as the Ocr/MediaProbe contract tests: both FakeDocConverter
(golden masters) and LibreOfficeDocConverter (the real adapter, its
find_binary/subprocess.run stubbed -- never a real soffice invocation)
must satisfy the Protocol and share the same raise-on-failure contract."""

import subprocess

import pytest

from notely.adapters.libreoffice_doc_converter import LibreOfficeDocConverter
from notely.ports import DocConverter, DocConverterError
from tests.fakes import FakeDocConverter


def test_both_implementations_satisfy_the_protocol():
    assert isinstance(FakeDocConverter(), DocConverter)
    assert isinstance(LibreOfficeDocConverter(), DocConverter)


def test_fake_writes_a_real_pdf_file_and_returns_its_path(tmp_path):
    fake = FakeDocConverter(pdf_bytes=b"%PDF-1.4 fake\n%%EOF")
    input_path = tmp_path / "deck.pptx"
    out_dir = tmp_path / "out"

    pdf_path = fake.convert_to_pdf(input_path, out_dir)

    assert pdf_path == out_dir / "deck.pdf"
    assert pdf_path.read_bytes() == b"%PDF-1.4 fake\n%%EOF"


def test_fake_records_every_call(tmp_path):
    fake = FakeDocConverter()
    fake.convert_to_pdf(tmp_path / "a.pptx", tmp_path / "out_a")
    fake.convert_to_pdf(tmp_path / "b.pptx", tmp_path / "out_b")
    assert fake.calls == [
        (str(tmp_path / "a.pptx"), str(tmp_path / "out_a")),
        (str(tmp_path / "b.pptx"), str(tmp_path / "out_b")),
    ]


def test_fake_raises_configured_error_instead_of_converting(tmp_path):
    fake = FakeDocConverter(error=DocConverterError("soffice not found"))
    with pytest.raises(DocConverterError, match="soffice not found"):
        fake.convert_to_pdf(tmp_path / "deck.pptx", tmp_path / "out")


def test_real_adapter_raises_when_binary_not_found(tmp_path, monkeypatch):
    converter = LibreOfficeDocConverter()
    monkeypatch.setattr(converter, "find_binary", lambda: None)

    with pytest.raises(DocConverterError, match="not found"):
        converter.convert_to_pdf(tmp_path / "deck.pptx", tmp_path / "out")


def test_real_adapter_raises_when_subprocess_fails(tmp_path, monkeypatch):
    converter = LibreOfficeDocConverter()
    monkeypatch.setattr(converter, "find_binary", lambda: "/usr/bin/soffice")

    def _raise(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"conversion error")

    monkeypatch.setattr(subprocess, "run", _raise)

    with pytest.raises(DocConverterError, match="conversion failed"):
        converter.convert_to_pdf(tmp_path / "deck.pptx", tmp_path / "out")


def test_real_adapter_raises_when_output_file_is_missing(tmp_path, monkeypatch):
    converter = LibreOfficeDocConverter()
    monkeypatch.setattr(converter, "find_binary", lambda: "/usr/bin/soffice")
    # subprocess "succeeds" but writes nothing -- e.g. soffice silently
    # skipped an unreadable/corrupt input file
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: None)

    with pytest.raises(DocConverterError, match="did not produce"):
        converter.convert_to_pdf(tmp_path / "deck.pptx", tmp_path / "out")


def test_real_adapter_returns_pdf_path_on_success(tmp_path, monkeypatch):
    converter = LibreOfficeDocConverter()
    monkeypatch.setattr(converter, "find_binary", lambda: "/usr/bin/soffice")
    out_dir = tmp_path / "out"

    def _fake_run(cmd, **kwargs):
        # mimic soffice actually writing the converted file
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "deck.pdf").write_bytes(b"%PDF-1.4\n%%EOF")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    pdf_path = converter.convert_to_pdf(tmp_path / "deck.pptx", out_dir)

    assert pdf_path == out_dir / "deck.pdf"
    assert pdf_path.exists()


def test_real_adapter_find_binary_prefers_path_over_app_bundle(monkeypatch):
    import shutil

    from notely.adapters import libreoffice_doc_converter as mod

    converter = LibreOfficeDocConverter()
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/soffice" if name == "soffice" else None)
    monkeypatch.setattr(mod.Path, "exists", lambda self: True)

    assert converter.find_binary() == "/usr/bin/soffice"


def test_real_adapter_find_binary_returns_none_when_nothing_found(monkeypatch):
    import shutil

    from notely.adapters import libreoffice_doc_converter as mod

    converter = LibreOfficeDocConverter()
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(mod.Path, "exists", lambda self: False)

    assert converter.find_binary() is None
