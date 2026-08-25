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
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO_URLS_PATH = ROOT / "input" / "video_urls.json"
VIDEOS_DIR = ROOT / "input" / "videos"
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
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

PRIVATE_MARKERS = ("private video", "sign in")


def load_video_urls():
    if not VIDEO_URLS_PATH.exists():
        print(f"ERROR: {VIDEO_URLS_PATH} not found", file=sys.stderr)
        sys.exit(1)
    with open(VIDEO_URLS_PATH) as f:
        return json.load(f)


def verify_video(path: Path) -> bool:
    """Return True if path exists, is non-empty, and ffprobe reports a positive duration."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    try:
        return float(result.stdout.strip()) > 0
    except ValueError:
        return False


def source_path(lecture_id: str) -> Path:
    return VIDEOS_DIR / f"{lecture_id}{SOURCE_SUFFIX}"


def read_source_url(path: Path) -> str | None:
    """URL recorded for an already-downloaded video, or None when there is no
    usable record -- a file fetched before this sidecar existed, or one whose
    sidecar is missing/corrupt. None always means "can't vouch for this
    file", which callers treat as a cache miss."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    url = data.get("url") if isinstance(data, dict) else None
    return url if isinstance(url, str) else None


def write_source_url(path: Path, url: str) -> None:
    """Temp file + atomic rename, matching the stage scripts' artifact writes
    (see scripts/04, 05, 06, 07). A torn write would only ever read back as
    "no record" and force a re-download, but there's no reason to leave that
    to chance."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps({"url": url}, indent=2))
    tmp.replace(path)


class _YtDlpResult:
    """Mimics the CompletedProcess fields fetch_lecture inspects."""

    def __init__(self, returncode: int, output: str):
        self.returncode = returncode
        # stdout and stderr are merged during streaming; expose the combined
        # text as .stderr since that's where the private-video markers land.
        self.stderr = output


def run_yt_dlp(url: str, output_path: Path, cookies_browser: str | None) -> _YtDlpResult:
    cmd = [
        # -m yt_dlp with the running interpreter, so the venv's yt-dlp is
        # found even when .venv/bin isn't on PATH.
        sys.executable, "-m", "yt_dlp",
        "-f", FORMAT,
        "--merge-output-format", "mp4",
        # one "[download]  NN.N%" line per progress tick, so callers (and the
        # web UI) can watch download progress instead of a silent blob.
        "--newline", "--progress",
        "-o", str(output_path),
    ]
    # yt-dlp needs a JS runtime to run YouTube's player code; without one it
    # falls back to legacy clients whose stream URLs YouTube now 500s on.
    # deno is yt-dlp's default (enabled automatically if present); if only
    # node is installed, enable that instead.
    if not shutil.which("deno") and shutil.which("node"):
        cmd += ["--js-runtimes", "node"]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    # "--" separates options from the positional URL -- defense in depth in
    # case a malformed/malicious value ever lands in video_urls.json outside
    # the web UI's own validation (webui/config.py's URL check).
    cmd += ["--", url]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    lines = []
    for line in proc.stdout:
        line = line.rstrip("\n")
        lines.append(line)
        print(line, flush=True)
    returncode = proc.wait()
    return _YtDlpResult(returncode, "\n".join(lines))


def fetch_lecture(lecture_id: str, url: str, force: bool) -> bool:
    output_path = VIDEOS_DIR / f"{lecture_id}.mp4"
    source_marker = source_path(lecture_id)

    # The cache hits only when the file on disk is playable *and* provably
    # came from the URL currently configured for this lecture id. Ids are
    # reused across courses (lecture01 is always lecture01), so filename
    # alone can't tell a fresh download from last term's leftovers.
    if not force and verify_video(output_path):
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

    result = run_yt_dlp(url, output_path, cookies_browser=None)

    if result.returncode != 0:
        stderr_lower = result.stderr.lower()
        if any(marker in stderr_lower for marker in PRIVATE_MARKERS):
            cookies_browser = os.environ.get("YOUTUBE_COOKIES_BROWSER", "chrome")
            print(
                f"[{lecture_id}] video appears private/sign-in-required; "
                f"retrying with --cookies-from-browser {cookies_browser}"
            )
            result = run_yt_dlp(url, output_path, cookies_browser=cookies_browser)

    if result.returncode != 0:
        print(f"[{lecture_id}] ERROR: yt-dlp failed:\n{result.stderr}", file=sys.stderr)
        if output_path.exists():
            output_path.unlink()
        return False

    if not verify_video(output_path):
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

    if not args.all and not args.lecture_id:
        parser.error("provide a lecture_id or use --all")
    if args.all and args.lecture_id:
        parser.error("provide either a lecture_id or --all, not both")

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
