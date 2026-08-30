"""Tests for scripts/00_fetch_videos.py's download cache.

The cache is keyed on the URL recorded in the <lecture_id>.source.json
sidecar, not on the output filename alone. Lecture ids are reused across
courses, so a filename-only cache silently serves last term's video to every
downstream stage when video_urls.json is re-pointed.
"""

import json

from conftest import load_stage

fetch_videos = load_stage("00_fetch_videos.py")

URL_A = "https://youtu.be/aaaaaaaaaaa"
URL_B = "https://youtu.be/bbbbbbbbbbb"


def _stub_yt_dlp(monkeypatch, videos_dir, *, succeeds=True):
    """Point the module at a tmp videos dir and replace the network call with
    a stub that just writes a file. Returns the list of URLs it was asked to
    download, so tests can assert on cache hits/misses."""
    monkeypatch.setattr(fetch_videos, "VIDEOS_DIR", videos_dir)
    monkeypatch.setattr(fetch_videos, "verify_video", lambda p: p.exists() and p.stat().st_size > 0)
    downloaded = []

    def fake_run_yt_dlp(url, output_path, cookies_browser):
        downloaded.append(url)
        if succeeds:
            output_path.write_bytes(b"fake mp4 bytes")
            return fetch_videos._YtDlpResult(0, "")
        return fetch_videos._YtDlpResult(1, "ERROR: something went wrong")

    monkeypatch.setattr(fetch_videos, "run_yt_dlp", fake_run_yt_dlp)
    return downloaded


def test_records_source_url_after_successful_download(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    downloaded = _stub_yt_dlp(monkeypatch, videos)

    assert fetch_videos.fetch_lecture("lecture01", URL_A, force=False) is True

    assert downloaded == [URL_A]
    sidecar = videos / "lecture01.source.json"
    assert json.loads(sidecar.read_text()) == {"url": URL_A}


def test_skips_download_when_recorded_url_still_matches(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    downloaded = _stub_yt_dlp(monkeypatch, videos)
    fetch_videos.fetch_lecture("lecture01", URL_A, force=False)
    downloaded.clear()

    assert fetch_videos.fetch_lecture("lecture01", URL_A, force=False) is True

    assert downloaded == []  # genuine cache hit


def test_redownloads_when_configured_url_changed(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    downloaded = _stub_yt_dlp(monkeypatch, videos)
    fetch_videos.fetch_lecture("lecture01", URL_A, force=False)
    downloaded.clear()

    # Same lecture id, different video -- the exact swap that used to be
    # ignored because input/videos/lecture01.mp4 already existed.
    assert fetch_videos.fetch_lecture("lecture01", URL_B, force=False) is True

    assert downloaded == [URL_B]
    sidecar = videos / "lecture01.source.json"
    assert json.loads(sidecar.read_text()) == {"url": URL_B}


def test_redownloads_when_video_predates_url_tracking(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    # A playable file with no sidecar: we can't prove where it came from, so
    # it must not be trusted as a cache hit.
    (videos / "lecture01.mp4").write_bytes(b"legacy download")
    downloaded = _stub_yt_dlp(monkeypatch, videos)

    assert fetch_videos.fetch_lecture("lecture01", URL_A, force=False) is True

    assert downloaded == [URL_A]


def test_corrupt_sidecar_is_treated_as_no_record(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "lecture01.mp4").write_bytes(b"some download")
    (videos / "lecture01.source.json").write_text("{ truncated")
    downloaded = _stub_yt_dlp(monkeypatch, videos)

    assert fetch_videos.fetch_lecture("lecture01", URL_A, force=False) is True

    assert downloaded == [URL_A]


def test_failed_download_leaves_no_sidecar_vouching_for_the_old_video(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    _stub_yt_dlp(monkeypatch, videos)
    fetch_videos.fetch_lecture("lecture01", URL_A, force=False)
    assert (videos / "lecture01.source.json").exists()

    # Re-point at a new URL, but the download fails this time.
    _stub_yt_dlp(monkeypatch, videos, succeeds=False)
    assert fetch_videos.fetch_lecture("lecture01", URL_B, force=False) is False

    # The old record must be gone: leaving it would make the *next* run treat
    # whatever is on disk as a valid download of URL_A.
    assert not (videos / "lecture01.source.json").exists()


def test_force_redownloads_and_refreshes_the_record(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    downloaded = _stub_yt_dlp(monkeypatch, videos)
    fetch_videos.fetch_lecture("lecture01", URL_A, force=False)
    downloaded.clear()

    assert fetch_videos.fetch_lecture("lecture01", URL_A, force=True) is True

    assert downloaded == [URL_A]
    assert json.loads((videos / "lecture01.source.json").read_text()) == {"url": URL_A}
