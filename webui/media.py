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

from notely.stages import STAGES_BY_NUMBER
from . import config
from .errors import NotFoundError, ServerError

log = logging.getLogger("notely.media")

# Guards guide_pdf() regeneration (C4): two concurrent requests both seeing a
# stale/missing PDF must not both spawn 08_export_pdf.py against the same
# output path. Module-level (not per-request) so it's shared across all
# requests/threads in this process.
_guide_pdf_lock = threading.Lock()

# Same reasoning as _guide_pdf_lock, shared across every exam paper/key --
# coarser than a per-file lock, but exam PDF export is rare/on-demand (not
# hammered the way the guide preview can be) and this is a single-user
# local tool (see webui/jobs.py's own docstring), so one lock for all of
# them is simplicity without a real cost.
_exam_pdf_lock = threading.Lock()

# Same reasoning again, shared across the course sheet and every
# per-lecture sheet.
_essentials_pdf_lock = threading.Lock()


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
            script = config.SCRIPTS_DIR / STAGES_BY_NUMBER[8].script
            fd, tmp_name = tempfile.mkstemp(dir=str(config.OUTPUT_DIR), suffix=".pdf.tmp")
            os.close(fd)
            tmp_path = Path(tmp_name)
            try:
                r = subprocess.run(
                    [sys.executable, str(script), "--output", str(tmp_path)],
                    capture_output=True,
                    text=True,
                    timeout=300,
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


def render_exam_pdf(name: str, key: bool = False) -> Path:
    """Render one generated exam paper (or, if key=True, its answer key)
    to PDF if the existing one (if any) is stale, and return its path.
    Same staleness-check + lock + atomic-rename shape as render_guide_pdf,
    parameterized over which exam and paper-vs-key via
    scripts/08_export_pdf.py's --exam/--key flags."""
    suffix = "_key" if key else ""
    md = config.OUTPUT_EXAMS_DIR / f"{name}{suffix}.md"
    if not md.exists():
        what = "answer key" if key else "exam"
        raise NotFoundError(f"no {what} found for {name} yet — generate it first")
    pdf = config.OUTPUT_EXAMS_DIR / f"{name}{suffix}.pdf"
    with _exam_pdf_lock:
        # Re-check staleness inside the lock, same reasoning as render_guide_pdf.
        if not pdf.exists() or pdf.stat().st_mtime < md.stat().st_mtime:
            script = config.SCRIPTS_DIR / STAGES_BY_NUMBER[8].script
            fd, tmp_name = tempfile.mkstemp(dir=str(config.OUTPUT_EXAMS_DIR), suffix=".pdf.tmp")
            os.close(fd)
            tmp_path = Path(tmp_name)
            argv = [sys.executable, str(script), "--exam", name, "--output", str(tmp_path)]
            if key:
                argv.append("--key")
            try:
                r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
                if r.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size == 0:
                    log.error(
                        "Exam PDF export failed (rc=%s): %s", r.returncode, (r.stderr or r.stdout)[-2000:]
                    )
                    raise ServerError("PDF export failed — see server logs")
                os.replace(tmp_path, pdf)
            finally:
                tmp_path.unlink(missing_ok=True)
    return pdf


def render_essentials_pdf(lecture_id: str | None) -> Path:
    """Render the course-level essentials sheet (lecture_id=None) or one
    lecture's sheet to PDF if the existing one (if any) is stale, and
    return its path. Same staleness-check + lock + atomic-rename shape as
    render_exam_pdf, parameterized over course vs lecture via
    scripts/08_export_pdf.py's --essentials flag (with or without a
    trailing lecture_id positional)."""
    if lecture_id:
        md = config.OUTPUT_ESSENTIALS_DIR / f"{lecture_id}.md"
        pdf = config.OUTPUT_ESSENTIALS_DIR / f"{lecture_id}.pdf"
        pdf_dir = config.OUTPUT_ESSENTIALS_DIR
    else:
        md = config.COURSE_ESSENTIALS_PATH
        pdf = config.OUTPUT_DIR / "essentials.pdf"
        pdf_dir = config.OUTPUT_DIR
    if not md.exists():
        what = f"essentials for {lecture_id}" if lecture_id else "course essentials"
        raise NotFoundError(f"no {what} found yet — generate it first")
    with _essentials_pdf_lock:
        # Re-check staleness inside the lock, same reasoning as render_guide_pdf.
        if not pdf.exists() or pdf.stat().st_mtime < md.stat().st_mtime:
            script = config.SCRIPTS_DIR / STAGES_BY_NUMBER[8].script
            fd, tmp_name = tempfile.mkstemp(dir=str(pdf_dir), suffix=".pdf.tmp")
            os.close(fd)
            tmp_path = Path(tmp_name)
            argv = [sys.executable, str(script), "--essentials", "--output", str(tmp_path)]
            if lecture_id:
                argv.insert(2, lecture_id)
            try:
                r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
                if r.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size == 0:
                    log.error(
                        "Essentials PDF export failed (rc=%s): %s",
                        r.returncode,
                        (r.stderr or r.stdout)[-2000:],
                    )
                    raise ServerError("PDF export failed — see server logs")
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
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(seek),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(tmp_path),
            ],
            capture_output=True,
            timeout=30,
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
