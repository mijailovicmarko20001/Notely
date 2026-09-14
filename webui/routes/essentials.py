"""Essentials sheet generation (stages 9/10) and course/per-lecture
preview + PDF export -- the web UI's "Essentials" tab. Generation runs
through the same job scheduler as pipeline stages (jobs.MANAGER), as a
[(lecture_id, 9, argv), ..., (None, 10, argv)] task list built directly
here rather than via notely.runner.build_tasks (which only knows about
the orchestrated 0-7 sweep) -- same "build the task list yourself, call
jobs.MANAGER.start_job" pattern webui/routes/exams.py already uses for
its own stage-11 generation.

Route order matters below: /essentials/course(/pdf) must be registered
*before* /essentials/{lecture_id}(/pdf), or FastAPI would match a request
for "course" against the dynamic route first (lecture_id="course",
rejected by validated_lecture_id) instead of the literal one."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from notely.pipeline.export import render_guide_html
from .. import config, essentials, jobs, media
from ..models import EssentialsGenerateRequest
from .common import validated_lecture_id

router = APIRouter(tags=["essentials"])


@router.get("/essentials")
def list_essentials():
    return essentials.list_essentials_state()


@router.post("/essentials/generate")
def generate_essentials(body: EssentialsGenerateRequest):
    lecture_ids = essentials.lectures_ready_for_essentials()
    if not lecture_ids:
        raise HTTPException(400, "no lecture has finished notes yet (run stage 6 first)")

    argv = ["--force"] if body.force else []
    tasks = [(lecture_id, 9, argv) for lecture_id in lecture_ids] + [(None, 10, argv)]
    # No busy pre-check: start_job() is the atomic claim-or-raise point,
    # same reasoning as routes/exams.py's own stage-11 generation.
    try:
        job_id = jobs.MANAGER.start_job(tasks)
    except jobs.Busy as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "job_id": job_id}


@router.get("/essentials/course")
def get_course_essentials():
    path = config.COURSE_ESSENTIALS_PATH
    if not path.exists():
        return {"exists": False}
    markdown = path.read_text(encoding="utf-8")
    return {"exists": True, "html": render_guide_html(markdown), "download": "/files/essentials.md"}


@router.get("/essentials/course/pdf")
def course_essentials_pdf():
    pdf = media.render_essentials_pdf(None)
    return FileResponse(pdf, media_type="application/pdf", filename="essentials.pdf")


@router.get("/essentials/{lecture_id}")
def get_lecture_essentials(lecture_id: str):
    lecture_id = validated_lecture_id(lecture_id)
    path = config.OUTPUT_ESSENTIALS_DIR / f"{lecture_id}.md"
    if not path.exists():
        return {"exists": False}
    markdown = path.read_text(encoding="utf-8")
    return {
        "exists": True,
        "html": render_guide_html(markdown),
        "download": f"/files/essentials/{lecture_id}.md",
    }


@router.get("/essentials/{lecture_id}/pdf")
def lecture_essentials_pdf(lecture_id: str):
    lecture_id = validated_lecture_id(lecture_id)
    pdf = media.render_essentials_pdf(lecture_id)
    return FileResponse(pdf, media_type="application/pdf", filename=f"{lecture_id}_essentials.pdf")
