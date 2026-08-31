"""In-process fakes for notely.ports's Protocols -- the only thing golden
master tests (tests/golden/, tests/test_golden.py) inject. No network, no
binaries, no SDK objects; deterministic given fixed inputs."""

from notely.ports import LlmApiError, LlmResponse, LlmUsage


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
