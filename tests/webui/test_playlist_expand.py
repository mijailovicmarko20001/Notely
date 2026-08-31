"""Characterization tests for webui/playlist.py's expand_playlist (previously
zero coverage -- test_playlist_api.py only exercises validate_video_url and
the HTTP layer's rejection paths, never the yt-dlp-driving success path).
subprocess.run is faked with canned yt-dlp -J JSON, matching its real
--flat-playlist -J output shape."""

import json
import subprocess

import pytest

from webui import playlist


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_run(stdout_obj=None, returncode=0, stdout_text=None, stderr=""):
    text = stdout_text if stdout_text is not None else json.dumps(stdout_obj)
    return lambda cmd, **kwargs: _FakeCompleted(returncode, stdout=text, stderr=stderr)


def test_single_video_url_returns_one_entry(monkeypatch):
    monkeypatch.setattr(
        playlist.subprocess,
        "run",
        _fake_run(
            {
                "_type": "video",
                "title": "Lecture 01",
                "webpage_url": "https://youtu.be/abc123",
                "duration": 3600,
            }
        ),
    )
    entries = playlist.expand_playlist("https://youtu.be/abc123")
    assert entries == [
        {"index": 0, "title": "Lecture 01", "url": "https://youtu.be/abc123", "duration": 3600}
    ]


def test_playlist_url_returns_ordered_entries(monkeypatch):
    monkeypatch.setattr(
        playlist.subprocess,
        "run",
        _fake_run(
            {
                "_type": "playlist",
                "entries": [
                    {"title": "Lecture 01", "webpage_url": "https://youtu.be/aaa", "duration": 100},
                    {"title": "Lecture 02", "webpage_url": "https://youtu.be/bbb", "duration": 200},
                ],
            }
        ),
    )
    entries = playlist.expand_playlist("https://youtube.com/playlist?list=xyz")
    assert [e["title"] for e in entries] == ["Lecture 01", "Lecture 02"]
    assert [e["index"] for e in entries] == [0, 1]


def test_playlist_filters_out_unavailable_null_entries(monkeypatch):
    monkeypatch.setattr(
        playlist.subprocess,
        "run",
        _fake_run(
            {
                "_type": "playlist",
                "entries": [
                    {"title": "Available", "webpage_url": "https://youtu.be/aaa", "duration": 100},
                    None,  # yt-dlp represents a deleted/private video this way
                ],
            }
        ),
    )
    entries = playlist.expand_playlist("https://youtube.com/playlist?list=xyz")
    assert len(entries) == 1
    assert entries[0]["title"] == "Available"


def test_entry_with_bare_video_id_gets_expanded_to_full_url(monkeypatch):
    # some yt-dlp flat-playlist entries carry just the video id in "url",
    # not "webpage_url"
    monkeypatch.setattr(
        playlist.subprocess,
        "run",
        _fake_run(
            {"_type": "playlist", "entries": [{"title": "Lecture 01", "url": "abc123", "duration": 60}]}
        ),
    )
    entries = playlist.expand_playlist("https://youtube.com/playlist?list=xyz")
    assert entries[0]["url"] == "https://www.youtube.com/watch?v=abc123"


def test_missing_title_falls_back_to_a_placeholder(monkeypatch):
    monkeypatch.setattr(
        playlist.subprocess,
        "run",
        _fake_run({"_type": "video", "webpage_url": "https://youtu.be/abc123", "duration": None}),
    )
    entries = playlist.expand_playlist("https://youtu.be/abc123")
    assert entries[0]["title"] == "video 1"
    assert entries[0]["duration"] is None


def test_nonzero_returncode_raises_playlist_error(monkeypatch):
    monkeypatch.setattr(
        playlist.subprocess, "run", _fake_run(stdout_text="", returncode=1, stderr="ERROR: video unavailable")
    )
    with pytest.raises(playlist.PlaylistError):
        playlist.expand_playlist("https://youtu.be/gone")


def test_unparseable_stdout_raises_playlist_error(monkeypatch):
    monkeypatch.setattr(playlist.subprocess, "run", _fake_run(stdout_text="not json", returncode=0))
    with pytest.raises(playlist.PlaylistError):
        playlist.expand_playlist("https://youtu.be/abc123")


def test_no_entries_raises_playlist_error(monkeypatch):
    monkeypatch.setattr(
        playlist.subprocess,
        "run",
        _fake_run({"_type": "playlist", "entries": []}),
    )
    with pytest.raises(playlist.PlaylistError):
        playlist.expand_playlist("https://youtube.com/playlist?list=empty")


def test_timeout_raises_playlist_error(monkeypatch):
    def _raise(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 60))

    monkeypatch.setattr(playlist.subprocess, "run", _raise)
    with pytest.raises(playlist.PlaylistError):
        playlist.expand_playlist("https://youtu.be/abc123", timeout=1)


def test_expand_playlist_validates_url_before_shelling_out(monkeypatch):
    calls = []
    monkeypatch.setattr(playlist.subprocess, "run", lambda *a, **k: calls.append(1))
    with pytest.raises(playlist.PlaylistError):
        playlist.expand_playlist("-o /etc/cron.d/x")
    assert calls == []
