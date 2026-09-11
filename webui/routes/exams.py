"""Practice exam generation and past-exam upload -- the web UI's "Exams"
tab. Generation runs through the same job scheduler as pipeline stages
(jobs.MANAGER), as a single stage-11 task built directly here rather than
via notely.runner.build_tasks (which only knows about the orchestrated
0-7 sweep) -- same "build the task list yourself, call
jobs.MANAGER.start_job" pattern routes/review.py already uses for its own
stage 5/6/7 re-run."""

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from notely.pipeline.export import render_guide_html
from .. import config, exams, jobs, media
from ..models import ExamGenerateRequest

router = APIRouter(tags=["exams"])


@router.post("/exams/upload")
async def upload_exams(files: list[UploadFile] = File(...)):
    saved = await exams.save_exam_uploads(files)
    return {"ok": True, "saved": saved}


@router.get("/exams")
def list_exams():
    return exams.list_exam_state()


@router.post("/exams/generate")
def generate_exams(body: ExamGenerateRequest):
    argv = []
    if body.count != 1:
        argv += ["--count", str(body.count)]
    if body.questions is not None:
        argv += ["--questions", str(body.questions)]
    if body.force:
        argv.append("--force")

    tasks = [(None, 11, argv)]
    # No busy pre-check: start_job() is the atomic claim-or-raise point,
    # same reasoning as routes/review.py's own stage 5/6/7 re-run.
    try:
        job_id = jobs.MANAGER.start_job(tasks)
    except jobs.Busy as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "job_id": job_id}


@router.get("/exams/{name}")
def get_exam(name: str):
    name = exams.validated_exam_name(name)
    path = config.OUTPUT_EXAMS_DIR / f"{name}.md"
    if not path.exists():
        return {"exists": False}
    markdown = path.read_text(encoding="utf-8")
    return {"exists": True, "html": render_guide_html(markdown), "download": f"/files/exams/{name}.md"}


@router.get("/exams/{name}/pdf")
def exam_pdf(name: str):
    name = exams.validated_exam_name(name)
    pdf = media.render_exam_pdf(name, key=False)
    return FileResponse(pdf, media_type="application/pdf", filename=f"{name}.pdf")


@router.get("/exams/{name}/key")
def get_exam_key(name: str):
    """A separate route from get_exam on purpose: the web UI only calls
    this once the user explicitly clicks "show answers" on that exam,
    rather than fetching (and rendering into the DOM) the key alongside
    the paper by default."""
    name = exams.validated_exam_name(name)
    path = config.OUTPUT_EXAMS_DIR / f"{name}_key.md"
    if not path.exists():
        return {"exists": False}
    markdown = path.read_text(encoding="utf-8")
    return {"exists": True, "html": render_guide_html(markdown), "download": f"/files/exams/{name}_key.md"}


@router.get("/exams/{name}/key/pdf")
def exam_key_pdf(name: str):
    name = exams.validated_exam_name(name)
    pdf = media.render_exam_pdf(name, key=True)
    return FileResponse(pdf, media_type="application/pdf", filename=f"{name}_key.pdf")
