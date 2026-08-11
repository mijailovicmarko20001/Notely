"""Expand a YouTube playlist (or single video) URL into an ordered entry list."""

import json
import subprocess
import sys


class PlaylistError(Exception):
    pass


def expand_playlist(url: str, timeout: int = 60) -> list:
    """Return [{index, title, url, duration}] for a playlist or single video URL."""
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "-J", "--no-warnings", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PlaylistError(f"yt-dlp timed out after {timeout}s")
    if result.returncode != 0:
        raise PlaylistError(result.stderr.strip() or "yt-dlp failed")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise PlaylistError("yt-dlp returned unparseable output")

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
