"""Combined /api router (A5): api.py had grown to 460 lines mixing settings,
lectures, slides, jobs, and review. Split by resource, mounted here under
the same /api prefix main.py used to get straight from api.router.

jobs.py itself later split three ways (Phase 6 of the cleanup plan): state
(dashboard snapshot), jobs (start/poll/cancel/SSE), guide (study guide
output) -- see each module's own docstring."""

from fastapi import APIRouter

from . import essentials, exams, guide, jobs, review, settings, slides, state

router = APIRouter(prefix="/api")
router.include_router(settings.router)
router.include_router(slides.router)
router.include_router(state.router)
router.include_router(jobs.router)
router.include_router(guide.router)
router.include_router(review.router)
router.include_router(exams.router)
router.include_router(essentials.router)
