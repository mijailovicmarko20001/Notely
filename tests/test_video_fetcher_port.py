"""Contract test for notely.ports.VideoFetcher (Phase 3, port 7 of 9).

Same shape as the other subprocess-backed ports: both FakeVideoFetcher
(golden masters) and YtDlpVideoFetcher (the real adapter, its
subprocess.Popen stubbed -- never a real yt-dlp/network call) must satisfy
the Protocol and share the same result shape."""

import subprocess

from notely.adapters.ytdlp_video_fetcher import YtDlpVideoFetcher
from notely.ports import VideoFetcher, VideoFetchResult
from tests.fakes import FakeVideoFetcher


class _FakeProc:
    def __init__(self, lines, returncode):
        self.stdout = lines
        self._returncode = returncode

    def wait(self):
        return self._returncode


def test_both_implementations_satisfy_the_protocol():
    assert isinstance(FakeVideoFetcher(), VideoFetcher)
    assert isinstance(YtDlpVideoFetcher(), VideoFetcher)


def test_fake_writes_a_video_file_on_success(tmp_path):
    fake = FakeVideoFetcher(returncode=0, video_bytes=b"fake mp4 bytes")
    output_path = tmp_path / "lecture01.mp4"

    result = fake.fetch("https://youtu.be/abc123", output_path, format="best")

    assert result == VideoFetchResult(0, "")
    assert output_path.read_bytes() == b"fake mp4 bytes"


def test_fake_does_not_write_a_file_on_failure(tmp_path):
    fake = FakeVideoFetcher(returncode=1, output="ERROR: private video")
    output_path = tmp_path / "lecture01.mp4"

    result = fake.fetch("https://youtu.be/abc123", output_path, format="best")

    assert result == VideoFetchResult(1, "ERROR: private video")
    assert not output_path.exists()


def test_fake_records_full_call_arguments(tmp_path):
    fake = FakeVideoFetcher()
    fake.fetch("https://youtu.be/abc123", tmp_path / "out.mp4", format="best", cookies_browser="chrome")

    assert fake.calls == [
        {
            "url": "https://youtu.be/abc123",
            "output_path": str(tmp_path / "out.mp4"),
            "format": "best",
            "cookies_browser": "chrome",
        }
    ]


def test_real_adapter_streams_stdout_lines_into_merged_output(monkeypatch, tmp_path, capsys):
    lines = iter(["[download]  10.0%\n", "[download]  50.0%\n", "[download] 100.0%\n"])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProc(lines, 0))

    result = YtDlpVideoFetcher().fetch("https://youtu.be/abc123", tmp_path / "out.mp4", format="best")

    assert result.returncode == 0
    assert result.output == "[download]  10.0%\n[download]  50.0%\n[download] 100.0%"
    # each line was also echoed live to stdout, not just buffered into .output
    captured = capsys.readouterr()
    assert "[download]  50.0%" in captured.out


def test_real_adapter_returns_nonzero_returncode_on_failure(monkeypatch, tmp_path):
    lines = iter(["ERROR: Video unavailable\n"])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProc(lines, 1))

    result = YtDlpVideoFetcher().fetch("https://youtu.be/abc123", tmp_path / "out.mp4", format="best")

    assert result.returncode == 1
    assert "ERROR: Video unavailable" in result.output


def test_real_adapter_command_includes_format_and_url(monkeypatch, tmp_path):
    captured = {}

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc(iter([]), 0)

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    YtDlpVideoFetcher().fetch("https://youtu.be/abc123", tmp_path / "out.mp4", format="bestvideo+bestaudio")

    cmd = captured["cmd"]
    assert "bestvideo+bestaudio" in cmd
    assert cmd[-1] == "https://youtu.be/abc123"
    assert "--" in cmd  # positional URL separated from options


def test_real_adapter_command_includes_cookies_browser_when_given(monkeypatch, tmp_path):
    captured = {}

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc(iter([]), 0)

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    YtDlpVideoFetcher().fetch(
        "https://youtu.be/abc123", tmp_path / "out.mp4", format="best", cookies_browser="chrome"
    )

    cmd = captured["cmd"]
    assert "--cookies-from-browser" in cmd
    assert cmd[cmd.index("--cookies-from-browser") + 1] == "chrome"
