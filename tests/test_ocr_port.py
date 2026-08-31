"""Contract test for notely.ports.Ocr (Phase 3, port 2 of 9).

Same shape as tests/test_llm_client_port.py: both FakeOcr (golden masters)
and TesseractOcr (the real adapter, its pytesseract call stubbed -- never a
real binary invocation) must satisfy the Protocol and share the same
never-raises-on-failure contract."""

from notely.adapters.tesseract_ocr import TesseractOcr
from notely.ports import Ocr
from tests.fakes import FakeOcr


def test_both_implementations_satisfy_the_protocol():
    assert isinstance(FakeOcr(), Ocr)
    assert isinstance(TesseractOcr(), Ocr)


def test_fake_returns_configured_text_for_a_known_path():
    fake = FakeOcr(texts={"frame_001.png": "Hello world"})
    assert fake.image_to_text("frame_001.png", lang="eng") == "Hello world"


def test_fake_returns_empty_string_for_an_unconfigured_path():
    # matches the real adapter's own failure-mode return value -- a test
    # fixture can leave a frame's OCR text unset to mean "OCR found nothing"
    fake = FakeOcr()
    assert fake.image_to_text("frame_999.png", lang="eng") == ""


def test_fake_records_every_call():
    fake = FakeOcr(texts={"a.png": "A"})
    fake.image_to_text("a.png", lang="eng")
    fake.image_to_text("b.png", lang="srp_latn+eng")
    assert fake.calls == [("a.png", "eng"), ("b.png", "srp_latn+eng")]


def test_real_adapter_reads_and_strips_pytesseract_output(tmp_path, monkeypatch):
    import pytesseract

    fake_image_path = tmp_path / "frame.png"
    from PIL import Image

    Image.new("RGB", (10, 10)).save(fake_image_path)

    monkeypatch.setattr(pytesseract, "image_to_string", lambda img, lang=None: "  Hello world  \n")

    result = TesseractOcr().image_to_text(fake_image_path, lang="eng")

    assert result == "Hello world"


def test_real_adapter_never_raises_on_ocr_failure(tmp_path, monkeypatch):
    import pytesseract

    fake_image_path = tmp_path / "frame.png"
    from PIL import Image

    Image.new("RGB", (10, 10)).save(fake_image_path)

    def _raise(img, lang=None):
        raise RuntimeError("tesseract binary not found")

    monkeypatch.setattr(pytesseract, "image_to_string", _raise)

    result = TesseractOcr().image_to_text(fake_image_path, lang="eng")

    assert result == ""


def test_real_adapter_never_raises_on_unreadable_image_path():
    result = TesseractOcr().image_to_text("/nonexistent/path/frame.png", lang="eng")
    assert result == ""
