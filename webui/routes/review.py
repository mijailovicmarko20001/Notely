"""Stage-4 review data and manual corrections -- the web UI's "Review" tab."""

from fastapi import APIRouter, HTTPException

from .. import config, jobs, review
from ..models import Corrections
from .common import validated_lecture_id

router = APIRouter(tags=["review"])


@router.get("/review/{lecture_id}")
def get_review(lecture_id: str):
    lecture_id = validated_lecture_id(lecture_id)
    try:
        return review.get_review_data(lecture_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@router.post("/review/{lecture_id}")
def post_review(lecture_id: str, body: Corrections):
    lecture_id = validated_lecture_id(lecture_id)
    corrections = [c.model_dump() for c in body.corrections]
    result = review.apply_corrections(lecture_id, corrections)
    # re-run downstream stages on the corrected timeline
    stages = [5, 6, 7] if config.get_api_key() else [5]
    tasks = jobs.build_tasks([lecture_id], stages, {}, True, bool(config.get_api_key()))
    # No busy pre-check: start_job() is the atomic claim-or-raise point (C2).
    # apply_corrections() above has already run by the time we know whether
    # a job could start; that's an accepted trade-off (the corrected
    # timeline is written either way) rather than a correctness race, since
    # only start_job()'s internal check decides who actually gets to run.
    try:
        job_id = jobs.MANAGER.start_job(tasks)
    except jobs.Busy as e:
        raise HTTPException(409, str(e))
    return {"ok": True, **result, "job_id": job_id}
