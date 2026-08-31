"""Contract test for notely.ports.LlmClient (Phase 3, port 1 of 9).

The point of a port: a stage written against LlmClient must behave
identically whether it's driven by the real adapter or the fake golden
master tests inject. This file pins that both:

- structurally satisfy the Protocol (isinstance, via @runtime_checkable),
- translate their underlying transport's response into the same
  LlmResponse(text, usage) shape,
- translate their underlying transport's failure into the same
  LlmApiError.

AnthropicLlmClient's own `messages.create` call is stubbed here (never a
real network call) -- this test is about the *translation* it performs,
not about exercising the Anthropic SDK itself."""

import pytest

from notely.adapters.anthropic_llm import AnthropicLlmClient
from notely.ports import LlmApiError, LlmClient, LlmUsage
from tests.fakes import FakeLlmClient, llm_response


class _FakeSdkTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeSdkUsage:
    def __init__(
        self, input_tokens, output_tokens, cache_creation_input_tokens=None, cache_read_input_tokens=None
    ):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        if cache_creation_input_tokens is not None:
            self.cache_creation_input_tokens = cache_creation_input_tokens
        if cache_read_input_tokens is not None:
            self.cache_read_input_tokens = cache_read_input_tokens


class _FakeSdkResponse:
    def __init__(self, content, usage):
        self.content = content
        self.usage = usage


@pytest.fixture()
def real_client_with_stubbed_sdk(monkeypatch):
    client = AnthropicLlmClient()  # real anthropic import, but no network call is ever made
    return client


def test_both_implementations_satisfy_the_protocol(real_client_with_stubbed_sdk):
    assert isinstance(FakeLlmClient(responses=[]), LlmClient)
    assert isinstance(real_client_with_stubbed_sdk, LlmClient)


def test_fake_returns_canned_responses_in_call_order():
    r1 = llm_response("first")
    r2 = llm_response("second")
    fake = FakeLlmClient(responses=[r1, r2])

    assert fake.create_message(model="m", max_tokens=10, messages=[]) is r1
    assert fake.create_message(model="m", max_tokens=10, messages=[]) is r2


def test_fake_records_every_request_payload():
    fake = FakeLlmClient(responses=[llm_response("x"), llm_response("y")])
    fake.create_message(model="claude-haiku-4-5", max_tokens=10, messages=[{"role": "user", "content": "a"}])
    fake.create_message(model="claude-sonnet-5", max_tokens=20, messages=[{"role": "user", "content": "b"}])

    assert [c["model"] for c in fake.calls] == ["claude-haiku-4-5", "claude-sonnet-5"]


def test_fake_raises_configured_error_instead_of_returning():
    fake = FakeLlmClient(error=LlmApiError("rate limited"))
    with pytest.raises(LlmApiError, match="rate limited"):
        fake.create_message(model="m", max_tokens=10, messages=[])


def test_real_adapter_translates_sdk_response_to_llm_response(real_client_with_stubbed_sdk, monkeypatch):
    sdk_response = _FakeSdkResponse(
        content=[_FakeSdkTextBlock("Hello "), _FakeSdkTextBlock("world")],
        usage=_FakeSdkUsage(input_tokens=42, output_tokens=7),
    )
    monkeypatch.setattr(real_client_with_stubbed_sdk._client.messages, "create", lambda **kw: sdk_response)

    result = real_client_with_stubbed_sdk.create_message(model="m", max_tokens=10, messages=[])

    assert result.text == "Hello \nworld"  # each text block joined with a newline, then stripped
    assert result.usage == LlmUsage(input_tokens=42, output_tokens=7)


def test_real_adapter_defaults_missing_cache_fields_to_zero(real_client_with_stubbed_sdk, monkeypatch):
    # a response with prompt caching NOT active on it -- the SDK's usage
    # object simply doesn't carry these attributes at all
    sdk_response = _FakeSdkResponse(
        content=[_FakeSdkTextBlock("ok")],
        usage=_FakeSdkUsage(input_tokens=1, output_tokens=1),
    )
    monkeypatch.setattr(real_client_with_stubbed_sdk._client.messages, "create", lambda **kw: sdk_response)

    result = real_client_with_stubbed_sdk.create_message(model="m", max_tokens=10, messages=[])

    assert result.usage.cache_creation_input_tokens == 0
    assert result.usage.cache_read_input_tokens == 0


def test_real_adapter_ignores_non_text_content_blocks(real_client_with_stubbed_sdk, monkeypatch):
    class _ImageBlock:
        type = "image"

    sdk_response = _FakeSdkResponse(
        content=[_ImageBlock(), _FakeSdkTextBlock("caption")],
        usage=_FakeSdkUsage(input_tokens=1, output_tokens=1),
    )
    monkeypatch.setattr(real_client_with_stubbed_sdk._client.messages, "create", lambda **kw: sdk_response)

    result = real_client_with_stubbed_sdk.create_message(model="m", max_tokens=10, messages=[])

    assert result.text == "caption"


def test_real_adapter_wraps_sdk_api_error_as_llm_api_error(real_client_with_stubbed_sdk, monkeypatch):
    import anthropic

    def _raise(**kw):
        raise anthropic.APIConnectionError(request=None)

    monkeypatch.setattr(real_client_with_stubbed_sdk._client.messages, "create", _raise)

    with pytest.raises(LlmApiError):
        real_client_with_stubbed_sdk.create_message(model="m", max_tokens=10, messages=[])
