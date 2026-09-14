"""Practice exam uploads and generated-artifact listing -- the web UI's
"Exams" tab business logic. Kept out of the route handlers so those stay
pure HTTP glue, same separation as decks.py/media.py.

Generation itself (format analysis + paper writing, stage 11) runs
through the job scheduler like every other stage -- see
webui/routes/exams.py, which builds a [(None, 11, argv)] task directly
(bypassing notely.runner.build_tasks, which only knows about the
orchestrated 0-7 sweep) and hands it to jobs.MANAGER.start_job(), the
same "build the task list yourself" pattern routes/review.py already
uses for its own stage 5/6/7 re-run.
"""

import re
from pathlib import Path

from fastapi import UploadFile

from . import config, decks
from .errors import ValidationError

# Generated exam names are always the pipeline's own exam_{NN:02d}
# numbering (notely.pipeline.exams.process_exam_generation), never a
# user-chosen string -- format is the only thing worth checking, unlike
# config.validate_lecture_id, which also checks membership in
# video_urls.json.
EXAM_NAME_RE = re.compile(r"^exam_\d{2,}$")


def validated_exam_name(name: str) -> str:
    """Every filesystem path built from a generated-exam name goes through
    this, so a traversal-shaped name never reaches an OUTPUT_EXAMS_DIR
    join -- same trust-boundary reasoning as
    routes/common.py::validated_lecture_id."""
    if not isinstance(name, str) or not EXAM_NAME_RE.match(name):
        raise ValidationError(f"invalid exam name: {str(name)[:60]!r}")
    return name


async def save_exam_uploads(files: list[UploadFile], max_bytes: int = config.MAX_UPLOAD_BYTES) -> list[str]:
    """Stream every uploaded past exam into input/exams/, additively.

    Unlike slide decks (webui.decks.save_pool_uploads replaces the whole
    pool per course, since a stale deck must never linger into a new
    course), there's no "current course" staleness concept for exam
    examples: more of them only improves stage 11's format blueprint, so
    each upload adds to the existing set rather than replacing it.

    PDF only, same reasoning as decks.py's pool mode: format-analysis text
    extraction (notely.pipeline.exams.extract_exam_pages) needs a real
    page structure a pptx doesn't have without a conversion pass.
    """
    config.EXAMS_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        name = (f.filename or "").strip()
        if not name.lower().endswith(".pdf"):
            raise ValidationError(f"{name or 'upload'}: only .pdf past exams are supported")
        dest = config.EXAMS_DIR / Path(name).name
        await decks.save_upload_stream(f, dest, max_bytes)
        saved.append(dest.name)
    return saved


def list_exam_state() -> dict:
    """Snapshot for GET /api/exams: uploaded format templates, generated
    papers (with whether each has an answer key yet), and whether a
    format blueprint is already cached (so the UI can explain what a
    non-forced "Generate" click will and won't redo)."""
    uploaded = sorted(p.name for p in config.EXAMS_DIR.glob("*.pdf")) if config.EXAMS_DIR.exists() else []

    generated = []
    if config.OUTPUT_EXAMS_DIR.exists():
        for p in sorted(config.OUTPUT_EXAMS_DIR.glob("exam_*.md")):
            if p.name.endswith("_key.md"):
                continue
            name = p.stem
            generated.append({"name": name, "has_key": (config.OUTPUT_EXAMS_DIR / f"{name}_key.md").exists()})

    return {
        "uploaded": uploaded,
        "generated": generated,
        "format_cached": (config.OUTPUT_EXAMS_DIR / "_format.json").exists(),
    }
