"""Ports: typing.Protocol seams between stage logic and the outside world.

Per the cleanup plan's Phase 3 -- a stage that calls an adapter through one
of these Protocols can be driven in a test by a fake, in-process, with no
network, no binaries, and deterministic output. Stage code depends on a
port; adapters (notely/adapters/) are injected at the entry point
(scripts/webui), never imported by stage logic directly.

One port lands per commit, each with its adapter and the stage(s) it
unblocks wired up in the same commit -- see CONTRIBUTING.md / the cleanup
plan for the full table. This file grows incrementally; don't pre-declare
ports whose stage hasn't been wired yet.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class LlmApiError(Exception):
    """Raised by an LlmClient implementation when the underlying API call
    fails, wrapping whatever the real SDK raised. Callers catch this
    instead of importing the anthropic SDK just for its exception type."""


@dataclass
class LlmUsage:
    input_tokens: int
    output_tokens: int
    # Only meaningful when prompt caching is active on the request -- 0
    # otherwise, never None, so callers can sum/print unconditionally.
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class LlmResponse:
    # Every text content block's text, concatenated and stripped -- callers
    # never see the raw content-block list (which may also carry non-text
    # blocks depending on the request).
    text: str
    usage: LlmUsage


@runtime_checkable
class LlmClient(Protocol):
    def create_message(self, **request_payload: Any) -> LlmResponse:
        """Send a Messages-API-shaped request (model, max_tokens, system,
        messages, temperature, ...) and return its concatenated text plus
        normalized usage. Raises LlmApiError on failure -- never returns
        None or a partial response."""
        ...


@runtime_checkable
class Ocr(Protocol):
    def image_to_text(self, image_path: Path, lang: str) -> str:
        """OCR the image at image_path using the given tesseract language
        spec (e.g. "srp_latn+eng"). Returns '' on any OCR failure -- never
        raises. Matches scripts/04_match_frames_to_slides.py's original
        ocr_frame contract: this stage's own docstring notes OCR failures
        shouldn't kill the whole run, so the port preserves that rather
        than pushing error handling onto every caller."""
        ...


@runtime_checkable
class MediaProbe(Protocol):
    def get_duration(self, path: Path) -> float | None:
        """Video duration in seconds via ffprobe, or None if the file
        doesn't exist, ffprobe isn't available/fails, or its output isn't a
        parseable number. Never raises. Collapses what were three separate,
        near-identical ffprobe subprocess implementations (scripts/00's
        verify_video, scripts/04's get_video_duration, webui/jobs.py's
        get_video_duration) into one."""
        ...


class DocConverterError(Exception):
    """Raised by a DocConverter implementation when conversion fails: the
    binary isn't installed, the conversion subprocess itself failed, or it
    silently didn't produce the expected output file."""


@runtime_checkable
class DocConverter(Protocol):
    def convert_to_pdf(self, input_path: Path, out_dir: Path) -> Path:
        """Convert input_path (e.g. a .pptx deck) to PDF, written into
        out_dir, and return the resulting PDF's path. Raises
        DocConverterError on any failure -- never returns a path that
        doesn't exist."""
        ...


class HtmlToPdfError(Exception):
    """Raised by an HtmlToPdf implementation when rendering fails: no
    Chrome/Chromium binary found, the print subprocess itself failed, or it
    silently didn't produce the expected PDF file."""


@runtime_checkable
class HtmlToPdf(Protocol):
    def render(self, html_uri: str, pdf_path: Path) -> None:
        """Render the page at html_uri (a file:// URI) to PDF at pdf_path
        via headless Chrome's --print-to-pdf. Raises HtmlToPdfError on any
        failure -- never leaves pdf_path missing without raising."""
        ...


class AudioExtractorError(Exception):
    """Raised by an AudioExtractor implementation when ffmpeg fails."""


@runtime_checkable
class AudioExtractor(Protocol):
    def extract_wav(self, video_path: Path, wav_path: Path) -> None:
        """Extract mono 16kHz PCM WAV audio from video_path to wav_path.
        Raises AudioExtractorError on failure."""
        ...

    def extract_compressed(self, video_path: Path, out_path: Path, bitrate: str = "24k") -> None:
        """Extract mono 16kHz Opus/Ogg audio from video_path to out_path --
        for backends with an upload size cap (see scripts/01_transcribe.py's
        transcribe_with_groq). Raises AudioExtractorError on failure."""
        ...


@dataclass
class VideoFetchResult:
    returncode: int
    # merged stdout+stderr, exactly as streamed to the console -- the
    # private-video markers fetch_lecture scans for land here
    output: str


@runtime_checkable
class VideoFetcher(Protocol):
    def fetch(
        self, url: str, output_path: Path, format: str, cookies_browser: str | None = None
    ) -> VideoFetchResult:
        """Download url to output_path via yt-dlp, streaming progress
        lines to stdout as they arrive -- the web UI's live progress bar
        depends on this happening in real time, not buffered until the
        process exits. Returns a result with .returncode and .output;
        doesn't raise for a yt-dlp failure itself (a normal, expected
        outcome the caller inspects via .returncode), only for a genuine
        OS-level problem launching the process."""
        ...
