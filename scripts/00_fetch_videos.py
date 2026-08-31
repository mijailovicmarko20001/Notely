#!/usr/bin/env python3
"""[0] Fetch lecture videos from YouTube via yt-dlp.

Usage:
    python scripts/00_fetch_videos.py <lecture_id>   # e.g. lecture01
    python scripts/00_fetch_videos.py --all           # fetch every entry
    python scripts/00_fetch_videos.py <lecture_id> --force  # re-download

Reads input/video_urls.json ({"lecture01": "https://youtu.be/...", ...})
and downloads each lecture to input/videos/<lecture_id>.mp4, recording the
URL it came from in input/videos/<lecture_id>.source.json.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Only needed to bootstrap the `from notely...` import below (finding
# notely/ on sys.path) -- notely.paths.PROJECT_ROOT is the same value and
# is what the rest of this file (and every other stage script) uses.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent

# notely/ (ports, adapters, paths) lives alongside scripts/ and webui/ at
# the project root, not on sys.path by default when this file is run
# directly (python scripts/00_fetch_videos.py) -- same fix
# tests/conftest.py applies for test discovery. Must happen before the
# `from notely...` import below.
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.adapters.ffprobe_media_probe import FfprobeMediaProbe  # noqa: E402
from notely.adapters.ytdlp_video_fetcher import YtDlpVideoFetcher  # noqa: E402
from notely.cli import require_lecture_id_or_all  # noqa: E402
from notely.io import load_json, save_json  # noqa: E402
from notely.paths import PROJECT_ROOT, VIDEO_URLS_PATH, VIDEOS_DIR  # noqa: E402

FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]"
# Sidecar recording which URL produced <lecture_id>.mp4. Without it the
# download cache is keyed on the output filename alone, so re-pointing a
# lecture id at a different video is silently ignored -- the stale file
# stays, and every downstream stage happily reprocesses the wrong lecture.
SOURCE_SUFFIX = ".source.json"

# Best-effort .env loading: only needed for YOUTUBE_COOKIES_BROWSER, which
# itself is only needed for private (not merely unlisted) videos.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

PRIVATE_MARKERS = ("private video", "sign in")


def load_video_urls():
    if not VIDEO_URLS_PATH.exists():
        print(f"ERROR: {VIDEO_URLS_PATH} not found", file=sys.stderr)
        sys.exit(1)
    return load_json(VIDEO_URLS_PATH)


def verify_video(path: Path, media_probe) -> bool:
    """Return True if path exists, is non-empty, and ffprobe reports a
    positive duration. media_probe: a MediaProbe (see notely.ports)."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    duration = media_probe.get_duration(path)
    return duration is not None and duration > 0


def source_path(lecture_id: str) -> Path:
    return VIDEOS_DIR / f"{lecture_id}{SOURCE_SUFFIX}"


def read_source_url(path: Path) -> str | None:
    """URL recorded for an already-downloaded video, or None when there is no
    usable record -- a file fetched before this sidecar existed, or one whose
    sidecar is missing/corrupt. None always means "can't vouch for this
    file", which callers treat as a cache miss."""
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    url = data.get("url") if isinstance(data, dict) else None
    return url if isinstance(url, str) else None


def write_source_url(path: Path, url: str) -> None:
    """Atomic write (see notely.io) -- a torn write would only ever read
    back as "no record" and force a re-download, but there's no reason to
    leave that to chance."""
    save_json(path, {"url": url})


def fetch_lecture(lecture_id: str, url: str, force: bool, media_probe=None, video_fetcher=None) -> bool:
    """media_probe: a MediaProbe (see notely.ports), defaults to the real
    ffprobe-backed adapter. video_fetcher: a VideoFetcher, defaults to the
    real yt-dlp-backed adapter. Both default to their real adapter; tests
    inject a fake instead of needing ffprobe/yt-dlp installed."""
    if media_probe is None:
        media_probe = FfprobeMediaProbe()
    if video_fetcher is None:
        video_fetcher = YtDlpVideoFetcher()
    output_path = VIDEOS_DIR / f"{lecture_id}.mp4"
    source_marker = source_path(lecture_id)

    # The cache hits only when the file on disk is playable *and* provably
    # came from the URL currently configured for this lecture id. Ids are
    # reused across courses (lecture01 is always lecture01), so filename
    # alone can't tell a fresh download from last term's leftovers.
    if not force and verify_video(output_path, media_probe):
        cached_url = read_source_url(source_marker)
        if cached_url == url:
            print(f"[{lecture_id}] cached, skipping")
            return True
        if cached_url is None:
            print(
                f"[{lecture_id}] cached video has no recorded source URL "
                f"(downloaded before URL tracking) — re-downloading rather "
                f"than assume it came from {url}"
            )
        else:
            print(
                f"[{lecture_id}] configured URL changed since download "
                f"({cached_url} -> {url}) — re-downloading"
            )

    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[{lecture_id}] downloading from {url} ...")
    # Drop the old record up front: a sidecar must never outlive the video it
    # describes, or a failed re-download would leave the previous URL
    # vouching for a file that is now stale, partial, or gone.
    source_marker.unlink(missing_ok=True)

    result = video_fetcher.fetch(url, output_path, format=FORMAT, cookies_browser=None)

    if result.returncode != 0:
        output_lower = result.output.lower()
        if any(marker in output_lower for marker in PRIVATE_MARKERS):
            cookies_browser = os.environ.get("YOUTUBE_COOKIES_BROWSER", "chrome")
            print(
                f"[{lecture_id}] video appears private/sign-in-required; "
                f"retrying with --cookies-from-browser {cookies_browser}"
            )
            result = video_fetcher.fetch(url, output_path, format=FORMAT, cookies_browser=cookies_browser)

    if result.returncode != 0:
        print(f"[{lecture_id}] ERROR: yt-dlp failed:\n{result.output}", file=sys.stderr)
        if output_path.exists():
            output_path.unlink()
        return False

    if not verify_video(output_path, media_probe):
        print(
            f"[{lecture_id}] ERROR: downloaded file failed verification "
            f"(missing, empty, or zero duration) — deleting",
            file=sys.stderr,
        )
        if output_path.exists():
            output_path.unlink()
        return False

    write_source_url(source_marker, url)
    print(f"[{lecture_id}] download OK: {output_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Fetch lecture videos from YouTube.")
    parser.add_argument("lecture_id", nargs="?", help="e.g. lecture01")
    parser.add_argument("--all", action="store_true", help="fetch every entry in video_urls.json")
    parser.add_argument("--force", action="store_true", help="re-download even if a valid file is cached")
    args = parser.parse_args()

    require_lecture_id_or_all(parser, args)

    video_urls = load_video_urls()

    if args.all:
        lecture_ids = list(video_urls.keys())
    else:
        if args.lecture_id not in video_urls:
            print(f"ERROR: '{args.lecture_id}' not found in {VIDEO_URLS_PATH}", file=sys.stderr)
            sys.exit(1)
        lecture_ids = [args.lecture_id]

    failures = []
    for lecture_id in lecture_ids:
        ok = fetch_lecture(lecture_id, video_urls[lecture_id], force=args.force)
        if not ok:
            failures.append(lecture_id)

    if failures:
        print(f"FAILED: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
