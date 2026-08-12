"""Pydantic request models for the JSON API (A1).

These validate *shape* (types, ranges, required fields) so malformed bodies
yield 422 instead of a 500 from an unguarded `body["key"]` lookup. Lecture-id
*trust-boundary* validation (format + membership in video_urls.json) stays
centralized in `config.validate_lecture_id()` / the routers' shared
`validated_lecture_id()` helper -- these models deliberately don't
reimplement that check, they just carry lecture ids as plain strings.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class SettingsUpdate(BaseModel):
    """PUT /settings body. Unknown/extra keys are ignored (matching the old
    `dict` behavior); config.write_settings() re-validates control
    characters and length (S4) regardless of what passes here."""

    ANTHROPIC_API_KEY: Optional[str] = None
    WHISPER_MODEL: Optional[str] = None
    NOTES_MODEL: Optional[str] = None
    OCR_LANG: Optional[str] = None

    def to_updates(self) -> dict:
        return self.model_dump(exclude_none=True)


class PlaylistExpandRequest(BaseModel):
    url: str = Field(min_length=1)


class LectureEntry(BaseModel):
    title: str = ""
    url: str = Field(min_length=1)


class LectureEntries(BaseModel):
    """POST /lectures body: ordered [{title, url}] -> lecture01..NN."""

    entries: list[LectureEntry] = Field(min_length=1)


class JobRequest(BaseModel):
    """POST /jobs body. `lecture_ids` are format/membership-checked against
    video_urls.json by the route (see `validated_lecture_id`), not here."""

    lecture_ids: list[str] = Field(default_factory=list)
    stages: list[int] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    force: bool = False

    @field_validator("stages")
    @classmethod
    def _stages_in_range(cls, v: list[int]) -> list[int]:
        bad = [s for s in v if s < 0 or s > 7]
        if bad:
            raise ValueError(f"stages must be 0-7, got {bad}")
        return v


class Correction(BaseModel):
    timestamp: float = Field(ge=0)
    slide_number: Optional[int] = None


class Corrections(BaseModel):
    """POST /review/{lecture_id} body."""

    corrections: list[Correction] = Field(min_length=1)
