"""Job execution: start/poll/cancel a pipeline run, and its SSE event
stream. Split out of what used to be routes/jobs.py -- Phase 6 of the
cleanup plan calls out that file's own docstring admitting it bundled
three concerns (state, job execution, study guide output); the dashboard
state snapshot moved to state.py, the study guide to guide.py."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from notely.stages import MAX_PIPELINE_STAGE
from .. import config, jobs
from ..models import JobRequest
from .common import validated_lecture_id

router = APIRouter(tags=["jobs"])


@router.post("/jobs")
def start_job(body: JobRequest):
    lecture_ids = [validated_lecture_id(x) for x in body.lecture_ids]
    stages = sorted(set(body.stages))
    if not stages or (not lecture_ids and stages != [MAX_PIPELINE_STAGE]):
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
