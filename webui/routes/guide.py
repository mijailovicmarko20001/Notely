"""Study guide output: the assembled markdown and its on-demand PDF
export. Split out of what used to be routes/jobs.py -- Phase 6 of the
cleanup plan calls out that file's own docstring admitting it bundled
three concerns (state, job execution, study guide output); this is the
last of the three, state.py and jobs.py hold the other two."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from .. import config, media

router = APIRouter(tags=["guide"])


@router.get("/guide")
def get_guide():
    path = config.OUTPUT_DIR / "study_guide.md"
    if not path.exists():
        return {"exists": False}
    return {"exists": True, "markdown": path.read_text(encoding="utf-8"), "download": "/files/study_guide.md"}


@router.get("/guide/pdf")
def guide_pdf():
    """Render the study guide to PDF (headless Chrome + MathJax) and return it."""
    pdf = media.render_guide_pdf()
    return FileResponse(pdf, media_type="application/pdf", filename="study_guide.pdf")
