"""Essentials sheets (stages 9/10) state listing -- the web UI's
"Essentials" tab business logic. Kept out of the route handlers so those
stay pure HTTP glue, same separation as exams.py/decks.py/media.py.

Generation itself (stage 9 per lecture, then stage 10 once over all of
them) runs through the job scheduler like every other stage -- see
webui/routes/essentials.py, which builds a
[(lecture_id, 9, argv), ..., (None, 10, argv)] task list directly
(bypassing notely.runner.build_tasks, which only knows about the
orchestrated 0-7 sweep) and hands it to jobs.MANAGER.start_job(), the same
"build the task list yourself" pattern webui/exams.py already uses for
its own stage-11 generation.
"""

from notely.io import load_json_or_default as load_json
from . import config, progress


def list_essentials_state() -> dict:
    """Snapshot for GET /api/essentials: every known lecture's notes/
    essentials status (so the UI can explain what "Generate" will and
    won't do -- a lecture with no notes yet gets skipped, not failed),
    plus whether the course-level sheet exists yet."""
    urls = load_json(config.VIDEO_URLS_PATH, {})
    lectures = [
        {
            "id": lecture_id,
            "has_notes": progress.artifact_ok(6, lecture_id),
            "has_essentials": progress.artifact_ok(9, lecture_id),
        }
        for lecture_id in sorted(urls)
    ]
    return {"lectures": lectures, "course_essentials": progress.artifact_ok(10)}


def lectures_ready_for_essentials() -> list[str]:
    """Every lecture with finished notes (stage 6) -- what a "Generate"
    click without an explicit lecture list processes. A lecture without
    notes yet is silently excluded rather than turned into a failed task:
    it just hasn't gotten there yet, same as run_pipeline.py --essentials
    only processing the lecture_ids it was given."""
    urls = load_json(config.VIDEO_URLS_PATH, {})
    return [lecture_id for lecture_id in sorted(urls) if progress.artifact_ok(6, lecture_id)]
