"""In-process fakes for notely.ports's Protocols -- the only thing golden
master tests (tests/golden/, tests/test_golden.py) inject. No network, no
binaries, no SDK objects; deterministic given fixed inputs."""

from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from notely.ports import (
    AudioExtractorError,
    DocConverterError,
    HtmlToPdfError,
    LlmApiError,
    LlmResponse,
    LlmUsage,
    TranscriberError,
    VideoFetchResult,
)


class FakeLlmClient:
    """Returns canned responses in call order; records every request payload
    it was called with so a test can assert on the prompt actually sent.

    `responses`: a list of LlmResponse to return, one per call, in order.
    Calling past the end of the list raises IndexError (a test that expects
    N calls should supply exactly N responses -- a stray extra call is a
    bug worth surfacing, not silently reusing the last response).

    `error`: if set, every call raises this (an LlmApiError) instead of
    returning a response -- for pinning a stage's failure-handling path.
    """

    def __init__(self, responses: list[LlmResponse] | None = None, error: LlmApiError | None = None):
        self._responses = list(responses or [])
        self._error = error
        self.calls: list[dict] = []

    def create_message(self, **request_payload) -> LlmResponse:
        self.calls.append(request_payload)
        if self._error is not None:
            raise self._error
        return self._responses.pop(0)


def llm_response(text: str, input_tokens: int = 100, output_tokens: int = 50, **usage_kwargs) -> LlmResponse:
    """Convenience builder for a canned FakeLlmClient response."""
    return LlmResponse(
        text=text, usage=LlmUsage(input_tokens=input_tokens, output_tokens=output_tokens, **usage_kwargs)
    )


class FakeOcr:
    """Maps image_path (str) -> canned OCR text, rather than an ordered
    queue like FakeLlmClient -- stage 4 OCRs a list of frame paths, and a
    test fixture naming its frames is a more natural and less brittle way
    to pin "this frame's text" than "the Nth call". Any path not in
    `texts` returns '' -- the same value the real adapter returns on an
    OCR failure, so a test can leave a frame's OCR text unset to simulate
    that. Records every (image_path, lang) it was asked to OCR."""

    def __init__(self, texts: dict[str, str] | None = None):
        self._texts = dict(texts or {})
        self.calls: list[tuple[str, str]] = []

    def image_to_text(self, image_path, lang: str) -> str:
        key = str(image_path)
        self.calls.append((key, lang))
        return self._texts.get(key, "")


class FakeMediaProbe:
    """Maps path (str) -> canned duration, same dict-keyed-by-path shape as
    FakeOcr and for the same reason. Any path not in `durations` returns
    None -- the real adapter's own value for "couldn't determine
    duration". Records every path it was asked to probe."""

    def __init__(self, durations: dict[str, float] | None = None):
        self._durations = dict(durations or {})
        self.calls: list[str] = []

    def get_duration(self, path) -> float | None:
        key = str(path)
        self.calls.append(key)
        return self._durations.get(key)


class FakeDocConverter:
    """Writes `pdf_bytes` (a real, minimal PDF -- see tests/pdf_fixtures.
    make_pdf_bytes) to <out_dir>/<input_path.stem>.pdf and returns that
    path, unlike FakeOcr/FakeMediaProbe's plain canned values: downstream
    code (render_pdf_to_images) needs an actual, parseable PDF on disk, not
    just a return value. Raises `error` instead, if configured."""

    def __init__(self, pdf_bytes: bytes | None = None, error: DocConverterError | None = None):
        self._pdf_bytes = pdf_bytes or b"%PDF-1.4\n%%EOF"
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def convert_to_pdf(self, input_path, out_dir) -> Path:
        self.calls.append((str(input_path), str(out_dir)))
        if self._error is not None:
            raise self._error
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = out_dir / f"{Path(input_path).stem}.pdf"
        pdf_path.write_bytes(self._pdf_bytes)
        return pdf_path


class FakeHtmlToPdf:
    """Writes `pdf_bytes` (a real, minimal PDF) to pdf_path -- same
    real-file-on-disk reasoning as FakeDocConverter, since callers (stage
    08's own main(), webui's render_guide_pdf) check the file actually
    exists and is non-empty afterward. Raises `error` instead, if
    configured. Records every (html_uri, pdf_path) it was asked to render,
    plus the HTML content actually on disk at html_uri at call time --
    stage 08's caller deletes that temp file immediately after render()
    returns, so a test asserting on the generated markup (math stashing,
    image path rewriting) has to capture it here, not read it back later."""

    def __init__(self, pdf_bytes: bytes | None = None, error: HtmlToPdfError | None = None):
        self._pdf_bytes = pdf_bytes or b"%PDF-1.4\n%%EOF"
        self._error = error
        self.calls: list[tuple[str, str]] = []
        self.rendered_html: list[str] = []

    def render(self, html_uri: str, pdf_path) -> None:
        self.calls.append((html_uri, str(pdf_path)))
        if html_uri.startswith("file://"):
            html_path = Path(url2pathname(urlparse(html_uri).path))
            if html_path.exists():
                self.rendered_html.append(html_path.read_text())
        if self._error is not None:
            raise self._error
        Path(pdf_path).write_bytes(self._pdf_bytes)


class FakeAudioExtractor:
    """Writes placeholder bytes to the target path, like FakeDocConverter/
    FakeHtmlToPdf -- downstream code checks a file exists there, but
    nothing in stage 1 decodes it as real audio until the Transcriber port
    (also Phase 3) lands and gets its own fake to hand the transcription
    backend. Raises `error` instead, if configured. Records every call,
    kept separate per method since they have different signatures."""

    def __init__(self, audio_bytes: bytes | None = None, error: AudioExtractorError | None = None):
        self._audio_bytes = audio_bytes or b"RIFF\x00\x00\x00\x00WAVEfmt "
        self._error = error
        self.wav_calls: list[tuple[str, str]] = []
        self.compressed_calls: list[tuple[str, str, str]] = []

    def extract_wav(self, video_path, wav_path) -> None:
        self.wav_calls.append((str(video_path), str(wav_path)))
        if self._error is not None:
            raise self._error
        Path(wav_path).write_bytes(self._audio_bytes)

    def extract_compressed(self, video_path, out_path, bitrate: str = "24k") -> None:
        self.compressed_calls.append((str(video_path), str(out_path), bitrate))
        if self._error is not None:
            raise self._error
        Path(out_path).write_bytes(self._audio_bytes)


class FakeVideoFetcher:
    """Returns a canned VideoFetchResult and, on a "successful" call
    (returncode 0), writes `video_bytes` to output_path -- the real
    adapter's actual point is producing that file, so callers that check
    output_path.exists() afterward (fetch_lecture's own verify_video call)
    need a fake that does too. Records every call's full argument set."""

    def __init__(self, returncode: int = 0, output: str = "", video_bytes: bytes | None = None):
        self._returncode = returncode
        self._output = output
        self._video_bytes = video_bytes or b"fake mp4 bytes"
        self.calls: list[dict] = []

    def fetch(self, url, output_path, format, cookies_browser=None) -> VideoFetchResult:
        self.calls.append(
            {
                "url": url,
                "output_path": str(output_path),
                "format": format,
                "cookies_browser": cookies_browser,
            }
        )
        if self._returncode == 0:
            Path(output_path).write_bytes(self._video_bytes)
        return VideoFetchResult(self._returncode, self._output)


class FakeTranscriber:
    """Returns a canned transcript dict, or raises `error` instead if
    configured. Records every call's full argument set."""

    def __init__(self, transcript: dict | None = None, error: TranscriberError | None = None):
        self._transcript = transcript or {"language": "en", "segments": []}
        self._error = error
        self.calls: list[dict] = []

    def transcribe(self, lecture_id, wav_path, model_size, forced_language, vocab_prompt) -> dict:
        self.calls.append(
            {
                "lecture_id": lecture_id,
                "wav_path": str(wav_path),
                "model_size": model_size,
                "forced_language": forced_language,
                "vocab_prompt": vocab_prompt,
            }
        )
        if self._error is not None:
            raise self._error
        return self._transcript
