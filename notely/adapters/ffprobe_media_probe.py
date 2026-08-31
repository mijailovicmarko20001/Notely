"""MediaProbe adapter backed by the ffprobe binary.

subprocess is a stdlib import (unlike pytesseract/anthropic, nothing to
lazily defer here) -- ffprobe itself is the external dependency, invoked
only inside get_duration.
"""

import subprocess
from pathlib import Path


class FfprobeMediaProbe:
    """Real MediaProbe. Never raises -- any failure (missing file, ffprobe
    not on PATH, nonzero exit, timeout, unparseable output) returns None."""

    def get_duration(self, path: Path) -> float | None:
        path = Path(path)
        if not path.exists():
            return None
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        if result.returncode != 0:
            return None
        try:
            return float(result.stdout.strip())
        except ValueError:
            return None
