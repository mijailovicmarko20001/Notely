"""LlmClient adapter backed by the real Anthropic SDK.

Lazily imports `anthropic` inside __init__ (not at module top level), same
convention as every stage script's own lazy imports -- `python -m
py_compile` and offline test collection work without the package
installed, and it's only ever constructed by an entry point (scripts/06,
scripts/07, webui's future stage 6/7 wiring), never by notely/ itself.
"""

from typing import Any

from ..ports import LlmApiError, LlmResponse, LlmUsage


class AnthropicLlmClient:
    """Real LlmClient. **client_kwargs are forwarded to anthropic.Anthropic
    (e.g. max_retries=5, matching every call site's existing convention)."""

    def __init__(self, **client_kwargs: Any):
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(**client_kwargs)

    def create_message(self, **request_payload: Any) -> LlmResponse:
        try:
            response = self._client.messages.create(**request_payload)
        except self._anthropic.APIError as e:
            raise LlmApiError(str(e)) from e

        text = "\n".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
        usage = LlmUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            # present only when prompt caching is active on this response
            cache_creation_input_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )
        return LlmResponse(text=text, usage=usage)
