"""Subprocess orchestration for the study-guide PDF export and video
preview-frame extraction (A2). Kept out of the route handlers so those stay
pure HTTP glue.
"""

import logging
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from . import config
from .errors import NotFoundError, ServerError

log = logging.getLogger("notely.media")

# Guards guide_pdf() regeneration (C4): two concurrent requests both seeing a
# stale/missing PDF must not both spawn 08_export_pdf.py against the same
# output path. Module-level (not per-request) so it's shared across all
# requests/threads in this process.
_guide_pdf_lock = threading.Lock()


def render_guide_pdf() -> Path:
    """Render the study guide to PDF (headless Chrome + MathJax) if the
    existing one (if any) is stale, and return its path."""
    md = config.OUTPUT_DIR / "study_guide.md"
    if not md.exists():
        raise NotFoundError("no study guide yet — run the pipeline first")
    pdf = config.OUTPUT_DIR / "study_guide.pdf"
    with _guide_pdf_lock:
        # Re-check staleness inside the lock: a request that queued behind
        # another one that just regenerated the PDF should see it as fresh
        # now and skip a redundant second render.
        if not pdf.exists() or pdf.stat().st_mtime < md.stat().st_mtime:
            script = config.SCRIPTS_DIR / "08_export_pdf.py"
            fd, tmp_name = tempfile.mkstemp(dir=str(config.OUTPUT_DIR), suffix=".pdf.tmp")
            os.close(fd)
            tmp_path = Path(tmp_name)
            try:
                r = subprocess.run(
                    [sys.executable, str(script), "--output", str(tmp_path)],
                    capture_output=True, text=True, timeout=300,
                )
                if r.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size == 0:
                    log.error("PDF export failed (rc=%s): %s", r.returncode, (r.stderr or r.stdout)[-2000:])
                    raise ServerError("PDF export failed — see server logs")
                # Atomic rename: a concurrent request reading `pdf` (e.g. one
                # that lost the staleness race and returned before this
                # request acquired the lock) never observes a
                # partially-written file.
                os.replace(tmp_path, pdf)
            finally:
                tmp_path.unlink(missing_ok=True)
    return pdf


def grab_preview_frame(lecture_id: str, t: float = 60.0) -> Path:
    """One frame from the lecture's own video, for the crop-region picker
    (FRONTEND_TODO.md's visual cropper). Deliberately doesn't depend on
    stage 3 having run -- crop is exactly the parameter stage 3 needs, so
    tuning it can't wait for stage 3's own output. Grabs a frame directly
    with ffmpeg instead, independent of the full sampling pipeline. Caller
    owns the returned temp file and must unlink it."""
    video_path = config.VIDEOS_DIR / f"{lecture_id}.mp4"
    if not video_path.exists():
        raise NotFoundError(f"no video for {lecture_id} yet — run stage 0 first")

    fd, tmp_name = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    tmp_path = Path(tmp_name)

    def _grab(seek: float) -> bool:
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(seek), "-i", str(video_path),
             "-frames:v", "1", "-q:v", "3", str(tmp_path)],
            capture_output=True, timeout=30,
        )
        return r.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0

    try:
        # t may be past a short video's end -- fall back to the first frame
        if not _grab(t) and not _grab(0):
            raise ServerError("ffmpeg could not extract a preview frame from this video")
    except subprocess.TimeoutExpired as exc:
        tmp_path.unlink(missing_ok=True)
        raise ServerError("timed out extracting a preview frame", status_code=504) from exc
    except FileNotFoundError as exc:
        tmp_path.unlink(missing_ok=True)
        raise ServerError("ffmpeg not found") from exc
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path
