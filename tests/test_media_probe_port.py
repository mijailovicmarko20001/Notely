"""Contract test for notely.ports.MediaProbe (Phase 3, port 3 of 9).

Same shape as the LlmClient/Ocr contract tests: both FakeMediaProbe (golden
masters) and FfprobeMediaProbe (the real adapter, its subprocess.run
stubbed -- never a real ffprobe invocation) must satisfy the Protocol and
share the same never-raises contract."""

import subprocess

from notely.adapters.ffprobe_media_probe import FfprobeMediaProbe
from notely.ports import MediaProbe
from tests.fakes import FakeMediaProbe


class _FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_both_implementations_satisfy_the_protocol():
    assert isinstance(FakeMediaProbe(), MediaProbe)
    assert isinstance(FfprobeMediaProbe(), MediaProbe)


def test_fake_returns_configured_duration_for_a_known_path():
    fake = FakeMediaProbe(durations={"lecture01.mp4": 3600.5})
    assert fake.get_duration("lecture01.mp4") == 3600.5


def test_fake_returns_none_for_an_unconfigured_path():
    fake = FakeMediaProbe()
    assert fake.get_duration("missing.mp4") is None


def test_fake_records_every_call():
    fake = FakeMediaProbe(durations={"a.mp4": 10.0})
    fake.get_duration("a.mp4")
    fake.get_duration("b.mp4")
    assert fake.calls == ["a.mp4", "b.mp4"]


def test_real_adapter_returns_none_when_file_does_not_exist(tmp_path):
    result = FfprobeMediaProbe().get_duration(tmp_path / "nonexistent.mp4")
    assert result is None


def test_real_adapter_parses_ffprobe_stdout(tmp_path, monkeypatch):
    video = tmp_path / "lecture01.mp4"
    video.write_bytes(b"fake video bytes")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(0, stdout="3600.512000\n"))

    result = FfprobeMediaProbe().get_duration(video)

    assert result == 3600.512


def test_real_adapter_returns_none_on_nonzero_returncode(tmp_path, monkeypatch):
    video = tmp_path / "lecture01.mp4"
    video.write_bytes(b"fake video bytes")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(1, stdout=""))

    result = FfprobeMediaProbe().get_duration(video)

    assert result is None


def test_real_adapter_returns_none_on_unparseable_stdout(tmp_path, monkeypatch):
    video = tmp_path / "lecture01.mp4"
    video.write_bytes(b"fake video bytes")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(0, stdout="N/A\n"))

    result = FfprobeMediaProbe().get_duration(video)

    assert result is None


def test_real_adapter_returns_none_on_timeout(tmp_path, monkeypatch):
    video = tmp_path / "lecture01.mp4"
    video.write_bytes(b"fake video bytes")

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)

    monkeypatch.setattr(subprocess, "run", _raise)

    result = FfprobeMediaProbe().get_duration(video)

    assert result is None


def test_real_adapter_returns_none_when_ffprobe_binary_is_missing(tmp_path, monkeypatch):
    video = tmp_path / "lecture01.mp4"
    video.write_bytes(b"fake video bytes")

    def _raise(*a, **k):
        raise FileNotFoundError("ffprobe not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    result = FfprobeMediaProbe().get_duration(video)

    assert result is None
