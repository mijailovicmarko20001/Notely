"""Small helpers shared across the /api routers."""

import json

from fastapi import HTTPException

from .. import config


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def validated_lecture_id(lecture_id: str) -> str:
    """Every filesystem path built from a lecture id goes through this --
    format + membership in video_urls.json, so a traversal-shaped id (e.g.
    `../../evil`) never reaches SLIDES_DIR/VIDEOS_DIR/OUTPUT_DIR joins.
    Centralizes S1's fix so no router reimplements the check."""
    try:
        return config.validate_lecture_id(lecture_id)
    except ValueError:
        raise HTTPException(422, f"invalid lecture id: {str(lecture_id)[:60]!r}")
