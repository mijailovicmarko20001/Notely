"""Small helpers shared across the /api routers."""

from fastapi import HTTPException

# Re-exported (not used directly in this file) -- routers do
# `from .common import load_json, validated_lecture_id`.
from notely.io import load_json_or_default as load_json  # noqa: F401
from .. import config


def validated_lecture_id(lecture_id: str) -> str:
    """Every filesystem path built from a lecture id goes through this --
    format + membership in video_urls.json, so a traversal-shaped id (e.g.
    `../../evil`) never reaches SLIDES_DIR/VIDEOS_DIR/OUTPUT_DIR joins.
    Centralizes S1's fix so no router reimplements the check."""
    try:
        return config.validate_lecture_id(lecture_id)
    except ValueError as exc:
        raise HTTPException(422, f"invalid lecture id: {str(lecture_id)[:60]!r}") from exc
