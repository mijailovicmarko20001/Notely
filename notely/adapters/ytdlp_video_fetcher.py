"""VideoFetcher adapter backed by yt-dlp (run as `python -m yt_dlp`, not a
separate binary, so the venv's install is always the one found -- see
fetch()'s use of sys.executable)."""

import shutil
import subprocess
import sys
from pathlib import Path

from ..ports import VideoFetchResult


class YtDlpVideoFetcher:
    """Real VideoFetcher, via yt-dlp."""

    def fetch(
        self, url: str, output_path: Path, format: str, cookies_browser: str | None = None
    ) -> VideoFetchResult:
        cmd = [
            # -m yt_dlp with the running interpreter, so the venv's yt-dlp
            # is found even when .venv/bin isn't on PATH.
            sys.executable,
            "-m",
            "yt_dlp",
            "-f",
            format,
            "--merge-output-format",
            "mp4",
            # one "[download]  NN.N%" line per progress tick, so callers
            # (and the web UI) can watch download progress instead of a
            # silent blob.
            "--newline",
            "--progress",
            "-o",
            str(output_path),
        ]
        # yt-dlp needs a JS runtime to run YouTube's player code; without
        # one it falls back to legacy clients whose stream URLs YouTube now
        # 500s on. deno is yt-dlp's default (enabled automatically if
        # present); if only node is installed, enable that instead.
        if not shutil.which("deno") and shutil.which("node"):
            cmd += ["--js-runtimes", "node"]
        if cookies_browser:
            cmd += ["--cookies-from-browser", cookies_browser]
        # "--" separates options from the positional URL -- defense in
        # depth in case a malformed/malicious value ever lands in
        # video_urls.json outside the web UI's own validation
        # (webui/config.py's URL check).
        cmd += ["--", url]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines = []
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            print(line, flush=True)
        returncode = proc.wait()
        return VideoFetchResult(returncode, "\n".join(lines))
