"""Contract test for notely.ports.Transcriber (Phase 3, port 8 of 9).

Covers the two local whisper backends (faster-whisper, mlx), which already
share this exact call shape -- see notely/ports.py's Transcriber docstring
for why WHISPER_BACKEND=groq stays a separate function rather than a third
implementation of this Protocol. Both FakeTranscriber (golden masters) and
the two real adapters (their underlying library calls stubbed -- never a
real model load or transcription) must satisfy the Protocol and share the
same raise-on-failure contract."""

import sys

import pytest

from notely.adapters.faster_whisper_transcriber import FasterWhisperTranscriber
from notely.adapters.mlx_transcriber import MlxTranscriber
from notely.ports import Transcriber, TranscriberError
from tests.fakes import FakeTranscriber


def test_all_implementations_satisfy_the_protocol():
    assert isinstance(FakeTranscriber(), Transcriber)
    assert isinstance(FasterWhisperTranscriber(), Transcriber)
    assert isinstance(MlxTranscriber(), Transcriber)


def test_fake_returns_configured_transcript():
    fake = FakeTranscriber(
        transcript={"language": "sr", "segments": [{"start": 0.0, "end": 1.0, "text": "Zdravo"}]}
    )
    result = fake.transcribe("lecture01", "audio.wav", "medium", None, "")
    assert result == {"language": "sr", "segments": [{"start": 0.0, "end": 1.0, "text": "Zdravo"}]}


def test_fake_records_full_call_arguments(tmp_path):
    fake = FakeTranscriber()
    fake.transcribe("lecture01", tmp_path / "audio.wav", "medium", "sr", "vocab terms")

    assert fake.calls == [
        {
            "lecture_id": "lecture01",
            "wav_path": str(tmp_path / "audio.wav"),
            "model_size": "medium",
            "forced_language": "sr",
            "vocab_prompt": "vocab terms",
        }
    ]


def test_fake_raises_configured_error_instead_of_transcribing():
    fake = FakeTranscriber(error=TranscriberError("model failed to load"))
    with pytest.raises(TranscriberError, match="model failed to load"):
        fake.transcribe("lecture01", "audio.wav", "medium", None, "")


# --- FasterWhisperTranscriber -------------------------------------------


def _install_fake_faster_whisper(monkeypatch, segments, language="en", probability=0.95):
    class _Info:
        def __init__(self):
            self.language = language
            self.language_probability = probability

    class _Segment:
        def __init__(self, start, end, text):
            self.start = start
            self.end = end
            self.text = text

    class _FakeModel:
        def __init__(self, model_size, cpu_threads, compute_type):
            pass

        def transcribe(self, path, **kwargs):
            return (iter([_Segment(**s) for s in segments]), _Info())

    fake_module = type(sys)("faster_whisper")
    fake_module.WhisperModel = _FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)


def test_faster_whisper_adapter_returns_segments_and_detected_language(tmp_path, monkeypatch):
    _install_fake_faster_whisper(
        monkeypatch,
        segments=[{"start": 0.0, "end": 1.5, "text": "  Hello world  "}],
        language="en",
    )

    result = FasterWhisperTranscriber().transcribe("lecture01", tmp_path / "audio.wav", "medium", None, "")

    assert result == {"language": "en", "segments": [{"start": 0.0, "end": 1.5, "text": "Hello world"}]}


def test_faster_whisper_adapter_raises_transcriber_error_on_failure(tmp_path, monkeypatch):
    class _FakeModel:
        def __init__(self, *a, **kw):
            raise RuntimeError("model file corrupt")

    fake_module = type(sys)("faster_whisper")
    fake_module.WhisperModel = _FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    with pytest.raises(TranscriberError, match="model file corrupt"):
        FasterWhisperTranscriber().transcribe("lecture01", tmp_path / "audio.wav", "medium", None, "")


def test_faster_whisper_adapter_raises_when_not_installed(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # simulates ImportError
    with pytest.raises(TranscriberError, match="not installed"):
        FasterWhisperTranscriber().transcribe("lecture01", tmp_path / "audio.wav", "medium", None, "")


# --- MlxTranscriber -------------------------------------------------------


def test_mlx_adapter_returns_segments_and_detected_language(tmp_path, monkeypatch):
    fake_module = type(sys)("mlx_whisper")
    fake_module.transcribe = lambda path, **kwargs: {
        "language": "en",
        "segments": [{"start": 0.0, "end": 2.0, "text": "  Hello from mlx  "}],
    }
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_module)

    result = MlxTranscriber().transcribe("lecture01", tmp_path / "audio.wav", "medium", None, "")

    assert result == {"language": "en", "segments": [{"start": 0.0, "end": 2.0, "text": "Hello from mlx"}]}


def test_mlx_adapter_raises_transcriber_error_on_failure(tmp_path, monkeypatch):
    def _raise(path, **kwargs):
        raise RuntimeError("GPU out of memory")

    fake_module = type(sys)("mlx_whisper")
    fake_module.transcribe = _raise
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_module)

    with pytest.raises(TranscriberError, match="GPU out of memory"):
        MlxTranscriber().transcribe("lecture01", tmp_path / "audio.wav", "medium", None, "")


def test_mlx_adapter_raises_when_not_installed(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx_whisper", None)  # simulates ImportError
    with pytest.raises(TranscriberError, match="not installed"):
        MlxTranscriber().transcribe("lecture01", tmp_path / "audio.wav", "medium", None, "")
