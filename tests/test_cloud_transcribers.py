"""Cloud transcription backends (Groq, OpenAI) -- notely.ports.CloudTranscriber
implementations. Written before the adapters exist (test-first).

No real network/SDK involved anywhere in this file: both providers' SDKs
are faked via sys.modules injection (same technique
test_transcriber_port.py uses for faster_whisper/mlx_whisper), and
notely.pipeline.transcribe_chunks's own chunking/retry logic is already
exhaustively covered by tests/test_transcribe_chunks.py -- these tests
pin that each adapter *wires into* that shared logic correctly (extracts
compressed audio, builds the right request, parses the response, chunks
when the file is too large, retries the right errors), not the shared
logic's own internals again.
"""

import sys
import types

import pytest
from fakes import FakeAudioExtractor

from notely.ports import CloudTranscriber, CloudTranscriberError

# --- fake SDK builders ------------------------------------------------------


class _FakeApiError(Exception):
    """Stand-in for groq/openai SDK exceptions that carry an HTTP status
    code (both SDKs are openai-client-shaped and do this)."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def _install_fake_sdk(monkeypatch, module_name, client_attr, create_fn):
    """Install a fake `module_name` module exposing `client_attr(api_key=...)`
    whose `.audio.transcriptions.create(**kwargs)` calls create_fn(**kwargs)."""

    class _Transcriptions:
        def create(self, **kwargs):
            return create_fn(**kwargs)

    class _Audio:
        def __init__(self):
            self.transcriptions = _Transcriptions()

    class _Client:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.audio = _Audio()

    fake_module = types.ModuleType(module_name)
    setattr(fake_module, client_attr, _Client)
    monkeypatch.setitem(sys.modules, module_name, fake_module)


def _sequenced_create_fn(*outcomes):
    """outcomes: a list of either an exception instance (raised) or a
    dict/object (returned), consumed in order across successive calls."""
    calls = []

    def create_fn(**kwargs):
        calls.append(kwargs)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    create_fn.calls = calls
    return create_fn


def _response(language, segments):
    return {"language": language, "segments": segments}


# =============================================================================
# Groq
# =============================================================================

from notely.adapters.groq_transcriber import GROQ_MAX_UPLOAD_MB, GroqTranscriber  # noqa: E402


def test_groq_satisfies_the_cloud_transcriber_protocol():
    assert isinstance(GroqTranscriber(), CloudTranscriber)


def test_groq_max_upload_constant_matches_documented_free_tier_cap():
    # a change here should be deliberate (checked against Groq's current
    # docs), not an accidental edit -- this pins the value so a diff shows up
    assert GROQ_MAX_UPLOAD_MB == 25


def test_groq_raises_when_api_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(CloudTranscriberError, match="GROQ_API_KEY"):
        GroqTranscriber().transcribe_video(
            "lecture01", tmp_path / "lecture01.mp4", None, "", FakeAudioExtractor()
        )


def test_groq_single_call_extracts_compressed_audio_and_parses_response(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn(_response("sr", [{"start": 0.0, "end": 1.5, "text": " Zdravo "}]))
    _install_fake_sdk(monkeypatch, "groq", "Groq", create_fn)
    extractor = FakeAudioExtractor(audio_bytes=b"x" * 100)  # tiny -- stays under any cap

    result = GroqTranscriber().transcribe_video(
        "lecture01", tmp_path / "lecture01.mp4", None, "vocab terms", extractor
    )

    assert result == {"language": "sr", "segments": [{"start": 0.0, "end": 1.5, "text": "Zdravo"}]}
    assert len(extractor.compressed_calls) == 1  # used extract_compressed, not extract_wav
    sent_kwargs = create_fn.calls[0]
    assert sent_kwargs["prompt"] == "vocab terms"
    assert sent_kwargs["response_format"] == "verbose_json"


def test_groq_forwards_forced_language(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn(_response("sr", []))
    _install_fake_sdk(monkeypatch, "groq", "Groq", create_fn)

    GroqTranscriber().transcribe_video(
        "lecture01", tmp_path / "lecture01.mp4", "sr", "", FakeAudioExtractor()
    )

    assert create_fn.calls[0]["language"] == "sr"


def test_groq_model_defaults_and_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn(_response("sr", []), _response("sr", []))
    _install_fake_sdk(monkeypatch, "groq", "Groq", create_fn)
    monkeypatch.delenv("GROQ_WHISPER_MODEL", raising=False)

    GroqTranscriber().transcribe_video("lecture01", tmp_path / "l.mp4", None, "", FakeAudioExtractor())
    assert create_fn.calls[0]["model"] == "whisper-large-v3-turbo"

    monkeypatch.setenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
    GroqTranscriber().transcribe_video("lecture01", tmp_path / "l.mp4", None, "", FakeAudioExtractor())
    assert create_fn.calls[1]["model"] == "whisper-large-v3"


def test_groq_retries_a_retryable_error_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn(
        _FakeApiError("rate limited", status_code=429),
        _response("sr", [{"start": 0.0, "end": 1.0, "text": "hi"}]),
    )
    _install_fake_sdk(monkeypatch, "groq", "Groq", create_fn)
    monkeypatch.setattr("time.sleep", lambda s: None)

    result = GroqTranscriber().transcribe_video(
        "lecture01", tmp_path / "l.mp4", None, "", FakeAudioExtractor()
    )

    assert result["segments"] == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    assert len(create_fn.calls) == 2


def test_groq_raises_cloud_transcriber_error_once_retries_exhausted(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn(*([_FakeApiError("still limited", status_code=429)] * 5))
    _install_fake_sdk(monkeypatch, "groq", "Groq", create_fn)
    monkeypatch.setattr("time.sleep", lambda s: None)

    with pytest.raises(CloudTranscriberError, match="still limited"):
        GroqTranscriber().transcribe_video("lecture01", tmp_path / "l.mp4", None, "", FakeAudioExtractor())


def test_groq_does_not_retry_a_non_retryable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn(_FakeApiError("bad request", status_code=400))
    _install_fake_sdk(monkeypatch, "groq", "Groq", create_fn)

    with pytest.raises(CloudTranscriberError, match="bad request"):
        GroqTranscriber().transcribe_video("lecture01", tmp_path / "l.mp4", None, "", FakeAudioExtractor())
    assert len(create_fn.calls) == 1


def test_groq_missing_segments_field_raises_cloud_transcriber_error_not_raw(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn({"language": "sr"})  # no "segments" key at all
    _install_fake_sdk(monkeypatch, "groq", "Groq", create_fn)

    with pytest.raises(CloudTranscriberError):
        GroqTranscriber().transcribe_video("lecture01", tmp_path / "l.mp4", None, "", FakeAudioExtractor())


def test_groq_chunks_when_compressed_audio_exceeds_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test")
    monkeypatch.setattr("notely.adapters.groq_transcriber.GROQ_MAX_UPLOAD_MB", 0)  # force chunking

    create_fn = _sequenced_create_fn(
        _response("sr", [{"start": 0.0, "end": 5.0, "text": "part one"}]),
        _response("sr", [{"start": 0.0, "end": 5.0, "text": "part two"}]),
    )
    _install_fake_sdk(monkeypatch, "groq", "Groq", create_fn)

    fake_chunks = [tmp_path / "c0.ogg", tmp_path / "c1.ogg"]
    for c in fake_chunks:
        c.write_bytes(b"x")
    monkeypatch.setattr(
        "notely.adapters.groq_transcriber.transcribe_chunks.split_audio_into_chunks",
        lambda audio_path, out_dir, segment_seconds: fake_chunks,
    )

    class _FakeProbe:
        def get_duration(self, path):
            return {str(fake_chunks[0]): 300.0, str(fake_chunks[1]): 120.0}.get(str(path), 999.0)

    monkeypatch.setattr("notely.adapters.groq_transcriber.FfprobeMediaProbe", lambda: _FakeProbe())

    extractor = FakeAudioExtractor(audio_bytes=b"x" * 1000)
    result = GroqTranscriber().transcribe_video("lecture01", tmp_path / "l.mp4", None, "", extractor)

    assert len(create_fn.calls) == 2
    assert result["segments"] == [
        {"start": 0.0, "end": 5.0, "text": "part one"},
        {"start": 300.0, "end": 305.0, "text": "part two"},
    ]


# =============================================================================
# OpenAI
# =============================================================================

from notely.adapters.openai_transcriber import OpenAiTranscriber  # noqa: E402


def test_openai_satisfies_the_cloud_transcriber_protocol():
    assert isinstance(OpenAiTranscriber(), CloudTranscriber)


def test_openai_raises_when_api_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(CloudTranscriberError, match="OPENAI_API_KEY"):
        OpenAiTranscriber().transcribe_video(
            "lecture01", tmp_path / "lecture01.mp4", None, "", FakeAudioExtractor()
        )


def test_openai_single_call_extracts_compressed_audio_and_parses_response(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn(_response("sr", [{"start": 0.0, "end": 1.5, "text": " Zdravo "}]))
    _install_fake_sdk(monkeypatch, "openai", "OpenAI", create_fn)
    extractor = FakeAudioExtractor(audio_bytes=b"x" * 100)

    result = OpenAiTranscriber().transcribe_video(
        "lecture01", tmp_path / "lecture01.mp4", None, "vocab terms", extractor
    )

    assert result == {"language": "sr", "segments": [{"start": 0.0, "end": 1.5, "text": "Zdravo"}]}
    assert len(extractor.compressed_calls) == 1
    assert create_fn.calls[0]["response_format"] == "verbose_json"


def test_openai_model_defaults_to_whisper1_for_timestamp_support(tmp_path, monkeypatch):
    # gpt-4o-transcribe/gpt-4o-mini-transcribe don't support
    # response_format=verbose_json / segment timestamps -- only whisper-1
    # does, and stage 5 needs segment timestamps to align the transcript
    # to slide windows, so the default must stay whisper-1 regardless of
    # which model is "newer"/"better" in general.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn(_response("sr", []))
    _install_fake_sdk(monkeypatch, "openai", "OpenAI", create_fn)
    monkeypatch.delenv("OPENAI_TRANSCRIBE_MODEL", raising=False)

    OpenAiTranscriber().transcribe_video("lecture01", tmp_path / "l.mp4", None, "", FakeAudioExtractor())

    assert create_fn.calls[0]["model"] == "whisper-1"


def test_openai_model_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1-custom")
    create_fn = _sequenced_create_fn(_response("sr", []))
    _install_fake_sdk(monkeypatch, "openai", "OpenAI", create_fn)

    OpenAiTranscriber().transcribe_video("lecture01", tmp_path / "l.mp4", None, "", FakeAudioExtractor())

    assert create_fn.calls[0]["model"] == "whisper-1-custom"


def test_openai_retries_a_retryable_error_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn(
        _FakeApiError("rate limited", status_code=429),
        _response("sr", [{"start": 0.0, "end": 1.0, "text": "hi"}]),
    )
    _install_fake_sdk(monkeypatch, "openai", "OpenAI", create_fn)
    monkeypatch.setattr("time.sleep", lambda s: None)

    result = OpenAiTranscriber().transcribe_video(
        "lecture01", tmp_path / "l.mp4", None, "", FakeAudioExtractor()
    )

    assert result["segments"] == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    assert len(create_fn.calls) == 2


def test_openai_raises_cloud_transcriber_error_once_retries_exhausted(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn(*([_FakeApiError("still limited", status_code=500)] * 5))
    _install_fake_sdk(monkeypatch, "openai", "OpenAI", create_fn)
    monkeypatch.setattr("time.sleep", lambda s: None)

    with pytest.raises(CloudTranscriberError, match="still limited"):
        OpenAiTranscriber().transcribe_video("lecture01", tmp_path / "l.mp4", None, "", FakeAudioExtractor())


def test_openai_does_not_retry_a_non_retryable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn(_FakeApiError("bad request", status_code=401))
    _install_fake_sdk(monkeypatch, "openai", "OpenAI", create_fn)

    with pytest.raises(CloudTranscriberError, match="bad request"):
        OpenAiTranscriber().transcribe_video("lecture01", tmp_path / "l.mp4", None, "", FakeAudioExtractor())
    assert len(create_fn.calls) == 1


def test_openai_missing_segments_field_raises_cloud_transcriber_error_not_raw(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    create_fn = _sequenced_create_fn({"language": "sr"})
    _install_fake_sdk(monkeypatch, "openai", "OpenAI", create_fn)

    with pytest.raises(CloudTranscriberError):
        OpenAiTranscriber().transcribe_video("lecture01", tmp_path / "l.mp4", None, "", FakeAudioExtractor())


def test_openai_chunks_when_compressed_audio_exceeds_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("notely.adapters.openai_transcriber.OPENAI_MAX_UPLOAD_MB", 0)

    create_fn = _sequenced_create_fn(
        _response("sr", [{"start": 0.0, "end": 5.0, "text": "part one"}]),
        _response("sr", [{"start": 0.0, "end": 5.0, "text": "part two"}]),
    )
    _install_fake_sdk(monkeypatch, "openai", "OpenAI", create_fn)

    fake_chunks = [tmp_path / "c0.ogg", tmp_path / "c1.ogg"]
    for c in fake_chunks:
        c.write_bytes(b"x")
    monkeypatch.setattr(
        "notely.adapters.openai_transcriber.transcribe_chunks.split_audio_into_chunks",
        lambda audio_path, out_dir, segment_seconds: fake_chunks,
    )

    class _FakeProbe:
        def get_duration(self, path):
            return {str(fake_chunks[0]): 300.0, str(fake_chunks[1]): 120.0}.get(str(path), 999.0)

    monkeypatch.setattr("notely.adapters.openai_transcriber.FfprobeMediaProbe", lambda: _FakeProbe())

    extractor = FakeAudioExtractor(audio_bytes=b"x" * 1000)
    result = OpenAiTranscriber().transcribe_video("lecture01", tmp_path / "l.mp4", None, "", extractor)

    assert len(create_fn.calls) == 2
    assert result["segments"] == [
        {"start": 0.0, "end": 5.0, "text": "part one"},
        {"start": 300.0, "end": 305.0, "text": "part two"},
    ]
