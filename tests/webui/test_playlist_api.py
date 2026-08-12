"""S5: argument injection into yt-dlp via user-supplied URLs.
`playlist.validate_video_url` requires http(s) + hostname, and is called
before any subprocess ever runs -- so these rejections are testable without
stubbing yt-dlp at all: a rejected URL never reaches subprocess.run()."""

import pytest

from webui.playlist import PlaylistError, validate_video_url


@pytest.mark.parametrize("bad_url", [
    "-o /etc/cron.d/x",
    "--exec touch pwned",
    "javascript:alert(1)",
    "file:///etc/passwd",
    "ftp://example.com/video",
    "",
    "not a url",
])
def test_validate_video_url_rejects_non_http_values(bad_url):
    with pytest.raises(PlaylistError):
        validate_video_url(bad_url)


def test_validate_video_url_accepts_https_with_hostname():
    url = "https://www.youtube.com/watch?v=abc123"
    assert validate_video_url(url) == url


def test_validate_video_url_rejects_http_url_without_hostname():
    with pytest.raises(PlaylistError):
        validate_video_url("http:///path-with-no-host")


# --- through the API ---------------------------------------------------

def test_playlist_expand_rejects_flag_shaped_url(client):
    resp = client.post("/api/playlist/expand", json={"url": "-o /etc/cron.d/x"})
    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body and "detail" in body


def test_playlist_expand_rejects_empty_url_body(client):
    resp = client.post("/api/playlist/expand", json={"url": ""})
    assert resp.status_code in (400, 422)


def test_playlist_expand_requires_url_field(client):
    resp = client.post("/api/playlist/expand", json={})
    assert resp.status_code == 422  # pydantic: url is required


def test_set_lectures_rejects_flag_shaped_url_in_entry(client):
    resp = client.post("/api/lectures", json={
        "entries": [{"title": "Lecture 1", "url": "-o /etc/cron.d/x"}],
    })
    assert resp.status_code == 422


def test_set_lectures_persists_valid_ordered_entries(client, project_root):
    resp = client.post("/api/lectures", json={
        "entries": [
            {"title": "Intro", "url": "https://youtu.be/aaaaaaaaaaa"},
            {"title": "Follow-up", "url": "https://youtu.be/bbbbbbbbbbb"},
        ],
    })
    assert resp.status_code == 200
    assert resp.json()["lectures"] == ["lecture01", "lecture02"]

    import json
    urls = json.loads((project_root / "input" / "video_urls.json").read_text())
    assert urls["lecture01"] == "https://youtu.be/aaaaaaaaaaa"
    assert urls["lecture02"] == "https://youtu.be/bbbbbbbbbbb"
