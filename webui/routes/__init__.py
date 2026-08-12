"""Combined /api router (A5): api.py had grown to 460 lines mixing settings,
lectures, slides, jobs, and review. Split by resource, mounted here under
the same /api prefix main.py used to get straight from api.router."""

from fastapi import APIRouter

from . import jobs, review, settings, slides

router = APIRouter(prefix="/api")
router.include_router(settings.router)
router.include_router(slides.router)
router.include_router(jobs.router)
router.include_router(review.router)
