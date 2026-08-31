"""Dashboard state snapshot (GET /state): per-lecture stage-completion
status, deck detection, and the current job snapshot. Split out of what
used to be routes/jobs.py -- Phase 6 of the cleanup plan calls out that
file's own docstring admitting it bundled three concerns (state, job
execution, study guide output); this is the first of the three, job
start/poll/cancel/SSE stays in jobs.py, the study guide moves to guide.py.
"""

from fastapi import APIRouter

from notely.stages import MAX_PIPELINE_STAGE, PER_LECTURE_STAGES
from .. import config, jobs, progress, review
from .common import load_json

router = APIRouter(tags=["state"])


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
        "study_guide": progress.artifact_ok(MAX_PIPELINE_STAGE),
        "has_api_key": bool(config.get_api_key()),
        "job": jobs.MANAGER.snapshot(),
        "busy": jobs.MANAGER.busy,
    }
