"""Expand a YouTube playlist (or single video) URL into an ordered entry list."""

import json
import logging
import subprocess
import sys
from urllib.parse import urlparse

log = logging.getLogger("notely.playlist")


class PlaylistError(Exception):
    pass


def validate_video_url(url: str) -> str:
    """Only http(s) URLs with a hostname are acceptable. This URL reaches
    yt-dlp's argv here and, once stored, again in stage 0 -- a value like
    `-o /etc/cron.d/x` would otherwise be parsed as a yt-dlp flag."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise PlaylistError("invalid URL: must be http:// or https:// with a hostname")
    return url


def expand_playlist(url: str, timeout: int = 60) -> list:
    """Return [{index, title, url, duration}] for a playlist or single video URL."""
    url = validate_video_url(url)
    # "--" separates yt-dlp's options from the positional URL.
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "-J", "--no-warnings", "--", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise PlaylistError(f"yt-dlp timed out after {timeout}s") from exc
    if result.returncode != 0:
        # yt-dlp's stderr can include local paths/environment details -- log
        # it server-side only, return a generic message to the client (S6).
        log.warning("yt-dlp failed for %r: %s", url, (result.stderr or "").strip()[-2000:])
        raise PlaylistError("could not fetch that URL — check it's a valid, accessible video/playlist link")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PlaylistError("yt-dlp returned unparseable output") from exc

    if data.get("_type") == "playlist" and "entries" in data:
        entries = [e for e in data["entries"] if e]  # unavailable videos come back as null
    else:
        entries = [data]

    out = []
    for i, e in enumerate(entries):
        video_url = e.get("webpage_url") or e.get("url") or ""
        if video_url and not video_url.startswith("http"):
            video_url = f"https://www.youtube.com/watch?v={video_url}"
        out.append(
            {
                "index": i,
                "title": e.get("title") or f"video {i + 1}",
                "url": video_url,
                "duration": e.get("duration"),
            }
        )
    if not out:
        raise PlaylistError("no videos found at that URL")
    return out
