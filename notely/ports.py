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
