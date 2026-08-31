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
