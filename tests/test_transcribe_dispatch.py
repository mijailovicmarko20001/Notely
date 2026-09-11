"""scripts/01_transcribe.py's backend dispatch (transcribe_lecture) --
written before the rewrite (test-first).

Previously an if/else chain: any WHISPER_BACKEND value other than "groq"
silently fell through to faster-whisper, including typos and unrecognized
values (a real footgun -- see D-something in notely/env.py's own D1-D3
precedent for this project's "silent default drift" bug class). This
covers the fix (unknown backend errors instead) plus the new registry-
based dispatch to CLOUD_TRANSCRIBER_BACKENDS (via CloudTranscriber.
transcribe_video) and LOCAL_TRANSCRIBER_BACKENDS (via Transcriber.
transcribe), and that a transcriber failure is caught and reported
cleanly (return False + stderr) rather than propagating a raw traceback
out of main() -- the wiring test in test_audio_extractor_port.py already
pins that explicit transcriber injection still bypasses backend selection
entirely, unchanged by this rewrite.
"""

import pytest
from fakes import FakeAudioExtractor

from conftest import load_stage
from notely.ports import AudioExtractorError, CloudTranscriberError, TranscriberError

s01 = load_stage("01_transcribe.py")


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(s01, "INPUT_VIDEOS_DIR", tmp_path / "input" / "videos")
    monkeypatch.setattr(s01, "OUTPUT_TRANSCRIPTS_DIR", tmp_path / "output" / "transcripts")
    monkeypatch.setattr(s01, "build_vocabulary_prompt", lambda lecture_id: "")
    video_dir = tmp_path / "input" / "videos"
    video_dir.mkdir(parents=True)
    (video_dir / "lecture01.mp4").write_bytes(b"fake video bytes")
    return tmp_path


class _FakeCloudTranscriber:
    def __init__(self, transcript=None, error=None):
        self._transcript = transcript or {"language": "sr", "segments": []}
        self._error = error
        self.calls = []

    def transcribe_video(self, lecture_id, video_path, forced_language, vocab_prompt, audio_extractor):
        self.calls.append(
            {
                "lecture_id": lecture_id,
                "video_path": str(video_path),
                "forced_language": forced_language,
                "vocab_prompt": vocab_prompt,
            }
        )
        if self._error is not None:
            raise self._error
        return self._transcript


def test_unknown_backend_returns_false_and_prints_a_clear_error(project, monkeypatch, capsys):
    monkeypatch.setenv("WHISPER_BACKEND", "not-a-real-backend")

    ok = s01.transcribe_lecture("lecture01", model_size="medium", force=True)

    assert ok is False
    err = capsys.readouterr().err
    assert "not-a-real-backend" in err
    assert "faster-whisper" in err and "groq" in err and "openai" in err  # lists the known ones


def test_unknown_backend_does_not_create_a_stray_temp_wav(project, monkeypatch):
    monkeypatch.setenv("WHISPER_BACKEND", "bogus")
    import tempfile
    from pathlib import Path

    before = set(Path(tempfile.gettempdir()).glob("lecture01_*.wav"))
    s01.transcribe_lecture("lecture01", model_size="medium", force=True)
    after = set(Path(tempfile.gettempdir()).glob("lecture01_*.wav"))
    assert after == before  # unknown backend fails before ever touching a wav path


def test_groq_backend_dispatches_via_cloud_transcriber_protocol(project, monkeypatch):
    monkeypatch.setenv("WHISPER_BACKEND", "groq")
    fake_cloud = _FakeCloudTranscriber(
        transcript={"language": "sr", "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}]}
    )
    monkeypatch.setattr(s01, "CLOUD_TRANSCRIBER_BACKENDS", {"groq": lambda: fake_cloud})

    ok = s01.transcribe_lecture("lecture01", model_size="medium", force=True)

    assert ok is True
    assert len(fake_cloud.calls) == 1
    assert fake_cloud.calls[0]["lecture_id"] == "lecture01"
    written = s01.load_json(s01.OUTPUT_TRANSCRIPTS_DIR / "lecture01.json")
    assert written["segments"] == [{"start": 0.0, "end": 1.0, "text": "hi"}]


def test_openai_backend_dispatches_via_cloud_transcriber_protocol(project, monkeypatch):
    monkeypatch.setenv("WHISPER_BACKEND", "openai")
    fake_cloud = _FakeCloudTranscriber(
        transcript={"language": "sr", "segments": [{"start": 0.0, "end": 2.0, "text": "hi"}]}
    )
    monkeypatch.setattr(
        s01, "CLOUD_TRANSCRIBER_BACKENDS", {"groq": lambda: None, "openai": lambda: fake_cloud}
    )

    ok = s01.transcribe_lecture("lecture01", model_size="medium", force=True)

    assert ok is True
    assert len(fake_cloud.calls) == 1


def test_cloud_backend_never_extracts_a_local_wav(project, monkeypatch):
    monkeypatch.setenv("WHISPER_BACKEND", "groq")
    fake_cloud = _FakeCloudTranscriber()
    monkeypatch.setattr(s01, "CLOUD_TRANSCRIBER_BACKENDS", {"groq": lambda: fake_cloud})
    extractor = FakeAudioExtractor()

    s01.transcribe_lecture("lecture01", model_size="medium", force=True, audio_extractor=extractor)

    assert extractor.wav_calls == []  # the cloud path does its own (compressed) extraction, not extract_wav


def test_cloud_transcriber_error_is_caught_and_returns_false(project, monkeypatch, capsys):
    monkeypatch.setenv("WHISPER_BACKEND", "groq")
    fake_cloud = _FakeCloudTranscriber(error=CloudTranscriberError("Groq transcription failed: boom"))
    monkeypatch.setattr(s01, "CLOUD_TRANSCRIBER_BACKENDS", {"groq": lambda: fake_cloud})

    ok = s01.transcribe_lecture("lecture01", model_size="medium", force=True)

    assert ok is False
    assert "boom" in capsys.readouterr().err
    assert not (s01.OUTPUT_TRANSCRIPTS_DIR / "lecture01.json").exists()


def test_local_transcriber_error_is_caught_and_returns_false(project, monkeypatch, capsys):
    monkeypatch.delenv("WHISPER_BACKEND", raising=False)

    class _FailingTranscriber:
        def transcribe(self, *a, **kw):
            raise TranscriberError("model load failed")

    ok = s01.transcribe_lecture(
        "lecture01",
        model_size="medium",
        force=True,
        audio_extractor=FakeAudioExtractor(),
        transcriber=_FailingTranscriber(),
    )

    assert ok is False
    assert "model load failed" in capsys.readouterr().err


def test_audio_extractor_error_during_local_extraction_is_caught(project, monkeypatch, capsys):
    monkeypatch.delenv("WHISPER_BACKEND", raising=False)
    extractor = FakeAudioExtractor(error=AudioExtractorError("ffmpeg not found"))

    ok = s01.transcribe_lecture(
        "lecture01", model_size="medium", force=True, audio_extractor=extractor, transcriber=object()
    )

    assert ok is False
    assert "ffmpeg not found" in capsys.readouterr().err


def test_injected_transcriber_still_bypasses_backend_selection_entirely(project, monkeypatch):
    # Regression guard: an explicitly injected transcriber must win
    # regardless of WHISPER_BACKEND's value, unchanged by the registry
    # rewrite -- test_audio_extractor_port.py's own wiring test covers the
    # successful-run shape of this; this pins it survives even a bogus
    # backend value the registry itself would reject.
    monkeypatch.setenv("WHISPER_BACKEND", "totally-bogus")
    calls = []

    class _RecordingTranscriber:
        def transcribe(self, lecture_id, wav_path, model_size, forced_language, vocab_prompt):
            calls.append(lecture_id)
            return {"language": "en", "segments": []}

    ok = s01.transcribe_lecture(
        "lecture01",
        model_size="medium",
        force=True,
        audio_extractor=FakeAudioExtractor(),
        transcriber=_RecordingTranscriber(),
    )

    assert ok is True
    assert calls == ["lecture01"]
