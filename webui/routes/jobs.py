"""Pipeline status and execution -- the dashboard state snapshot, the
web UI's "Run" tab (start/poll/cancel/SSE), and the study-guide output
(the pipeline's final artifact, so it lives alongside job status)."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from notely.stages import PER_LECTURE_STAGES
from .. import config, jobs, media, progress, review
from ..models import JobRequest
from .common import load_json, validated_lecture_id

router = APIRouter(tags=["jobs"])


@router.get("/state")
def get_state():
    urls = load_json(config.VIDEO_URLS_PATH, {})
    meta = load_json(config.LECTURES_META_PATH, {})
    lectures = []
    for lecture_id in sorted(urls):
        stages = {s: progress.artifact_ok(s, lecture_id) for s in PER_LECTURE_STAGES}
        deck = next(
            (
                p.name
                for ext in ("pptx", "pdf")
                for p in [config.SLIDES_DIR / f"{lecture_id}.{ext}"]
                if p.exists()
            ),
            None,
        )
        lectures.append(
            {
                "id": lecture_id,
                "url": urls[lecture_id],
                "title": meta.get(lecture_id, {}).get("title", lecture_id),
                "deck": deck,
                "stages": stages,
                # cheap (small-file-read only) so it's safe to include on every
                # state refresh -- lets the UI show a review-needed badge
                # without a per-lecture fetch loop
                "needs_review_count": review.count_low_confidence(lecture_id) if stages.get(4) else 0,
            }
        )
    return {
        "lectures": lectures,
        "study_guide": progress.artifact_ok(7),
        "has_api_key": bool(config.get_api_key()),
        "job": jobs.MANAGER.snapshot(),
        "busy": jobs.MANAGER.busy,
    }


@router.post("/jobs")
def start_job(body: JobRequest):
    lecture_ids = [validated_lecture_id(x) for x in body.lecture_ids]
    stages = sorted(set(body.stages))
    if not stages or (not lecture_ids and stages != [7]):
        raise HTTPException(400, "lecture_ids and stages required")
    options = {**config.DEFAULT_STAGE_OPTIONS, **body.options}
    tasks = jobs.build_tasks(lecture_ids, stages, options, body.force, bool(config.get_api_key()))
    if not tasks:
        raise HTTPException(400, "nothing to run (is stage 6 locked without an API key?)")
    # No busy pre-check here: two concurrent POSTs would both pass a
    # check-then-act race. start_job() claims the "one job at a time" slot
    # atomically and raises Busy if it loses (C2).
    try:
        job_id = jobs.MANAGER.start_job(tasks)
    except jobs.Busy as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "job_id": job_id}


@router.get("/jobs/current")
def current_job():
    return {"job": jobs.MANAGER.snapshot(), "busy": jobs.MANAGER.busy}


@router.post("/jobs/current/cancel")
def cancel_job():
    if not jobs.MANAGER.busy:
        raise HTTPException(409, "no job running")
    jobs.MANAGER.cancel()
    return {"ok": True}


@router.get("/jobs/current/events")
async def job_events(request: Request):
    last_id = request.headers.get("last-event-id")
    try:
        seq = int(last_id) if last_id else 0
    except ValueError:
        seq = 0

    async def gen(start_seq):
        seen = start_seq
        idle = 0.0
        while True:
            if await request.is_disconnected():
                return
            batch = jobs.MANAGER.events_since(seen)
            for evt in batch:
                seen = evt["seq"]
                yield f"id: {evt['seq']}\ndata: {json.dumps(evt)}\n\n"
            if batch:
                idle = 0.0
            else:
                idle += 0.25
                if idle >= 15.0:
                    yield ": heartbeat\n\n"
                    idle = 0.0
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(seq), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/guide")
def get_guide():
    path = config.OUTPUT_DIR / "study_guide.md"
    if not path.exists():
        return {"exists": False}
    return {"exists": True, "markdown": path.read_text(), "download": "/files/study_guide.md"}


@router.get("/guide/pdf")
def guide_pdf():
    """Render the study guide to PDF (headless Chrome + MathJax) and return it."""
    pdf = media.render_guide_pdf()
    return FileResponse(pdf, media_type="application/pdf", filename="study_guide.pdf")
