"""Contract test for notely.ports.AudioExtractor (Phase 3, port 6 of 9).

Same shape as the DocConverter/HtmlToPdf contract tests: both
FakeAudioExtractor (golden masters) and FfmpegAudioExtractor (the real
adapter, its subprocess.run stubbed -- never a real ffmpeg invocation)
must satisfy the Protocol and share the same raise-on-failure contract."""

import subprocess
from pathlib import Path

import pytest

from conftest import load_stage
from notely.adapters.ffmpeg_audio_extractor import FfmpegAudioExtractor
from notely.ports import AudioExtractor, AudioExtractorError
from tests.fakes import FakeAudioExtractor


class _FakeCompleted:
    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self.stderr = stderr


def test_both_implementations_satisfy_the_protocol():
    assert isinstance(FakeAudioExtractor(), AudioExtractor)
    assert isinstance(FfmpegAudioExtractor(), AudioExtractor)


def test_fake_writes_a_real_file_for_wav(tmp_path):
    fake = FakeAudioExtractor(audio_bytes=b"fake wav bytes")
    wav_path = tmp_path / "audio.wav"

    fake.extract_wav(tmp_path / "lecture01.mp4", wav_path)

    assert wav_path.read_bytes() == b"fake wav bytes"


def test_fake_writes_a_real_file_for_compressed(tmp_path):
    fake = FakeAudioExtractor(audio_bytes=b"fake opus bytes")
    out_path = tmp_path / "audio.ogg"

    fake.extract_compressed(tmp_path / "lecture01.mp4", out_path, bitrate="32k")

    assert out_path.read_bytes() == b"fake opus bytes"


def test_fake_records_calls_separately_per_method(tmp_path):
    fake = FakeAudioExtractor()
    fake.extract_wav(tmp_path / "a.mp4", tmp_path / "a.wav")
    fake.extract_compressed(tmp_path / "b.mp4", tmp_path / "b.ogg", bitrate="24k")

    assert fake.wav_calls == [(str(tmp_path / "a.mp4"), str(tmp_path / "a.wav"))]
    assert fake.compressed_calls == [(str(tmp_path / "b.mp4"), str(tmp_path / "b.ogg"), "24k")]


def test_fake_raises_configured_error_instead_of_extracting(tmp_path):
    fake = FakeAudioExtractor(error=AudioExtractorError("ffmpeg not found"))
    with pytest.raises(AudioExtractorError, match="ffmpeg not found"):
        fake.extract_wav(tmp_path / "lecture01.mp4", tmp_path / "audio.wav")


def test_real_adapter_raises_when_ffmpeg_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kwargs: _FakeCompleted(1, stderr=b"ffmpeg: invalid input")
    )

    with pytest.raises(AudioExtractorError, match="ffmpeg failed extracting audio"):
        FfmpegAudioExtractor().extract_wav(tmp_path / "lecture01.mp4", tmp_path / "audio.wav")


def test_real_adapter_raises_when_ffmpeg_binary_is_missing(tmp_path, monkeypatch):
    def _raise(cmd, **kwargs):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    with pytest.raises(AudioExtractorError):
        FfmpegAudioExtractor().extract_wav(tmp_path / "lecture01.mp4", tmp_path / "audio.wav")


def test_real_adapter_succeeds_silently_on_zero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: _FakeCompleted(0))

    FfmpegAudioExtractor().extract_wav(tmp_path / "lecture01.mp4", tmp_path / "audio.wav")  # no raise


def test_real_adapter_compressed_uses_opus_codec_and_given_bitrate(tmp_path, monkeypatch):
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    FfmpegAudioExtractor().extract_compressed(
        tmp_path / "lecture01.mp4", tmp_path / "audio.ogg", bitrate="32k"
    )

    cmd = captured["cmd"]
    assert "libopus" in cmd
    assert "32k" in cmd


# --- wiring: scripts/01_transcribe.py's transcribe_lecture actually uses
# the injected AudioExtractor (not a real ffmpeg call) and Transcriber (not
# a real whisper backend) -- see tests/test_transcriber_port.py for the
# Transcriber port's own contract test. -------------------------------


def test_transcribe_lecture_uses_the_injected_audio_extractor_and_transcriber(tmp_path, monkeypatch):
    s01 = load_stage("01_transcribe.py")
    monkeypatch.setattr(s01, "INPUT_VIDEOS_DIR", tmp_path / "input" / "videos")
    monkeypatch.setattr(s01, "OUTPUT_TRANSCRIPTS_DIR", tmp_path / "output" / "transcripts")
    monkeypatch.delenv("WHISPER_BACKEND", raising=False)
    video_dir = tmp_path / "input" / "videos"
    video_dir.mkdir(parents=True)
    (video_dir / "lecture01.mp4").write_bytes(b"fake video bytes")

    fake_extractor = FakeAudioExtractor(audio_bytes=b"fake wav bytes")
    seen_wav_bytes = {}

    class _RecordingTranscriber:
        def transcribe(self, lecture_id, wav_path, model_size, forced_language, vocab_prompt):
            # prove the *extracted* audio (from the fake, not real ffmpeg)
            # is what actually reaches the transcription backend
            seen_wav_bytes["content"] = Path(wav_path).read_bytes()
            return {"language": "en", "segments": []}

    monkeypatch.setattr(s01, "build_vocabulary_prompt", lambda lecture_id: "")

    ok = s01.transcribe_lecture(
        "lecture01",
        model_size="medium",
        force=True,
        audio_extractor=fake_extractor,
        transcriber=_RecordingTranscriber(),
    )

    assert ok is True
    assert len(fake_extractor.wav_calls) == 1
    video_path, wav_path = fake_extractor.wav_calls[0]
    assert video_path == str(video_dir / "lecture01.mp4")
    assert seen_wav_bytes["content"] == b"fake wav bytes"
