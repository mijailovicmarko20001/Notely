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

from notely.stages import MAX_PIPELINE_STAGE


class SettingsUpdate(BaseModel):
    """PUT /settings body. Unknown/extra keys are ignored (matching the old
    `dict` behavior); config.write_settings() re-validates control
    characters and length (S4) regardless of what passes here."""

    ANTHROPIC_API_KEY: Optional[str] = None
    WHISPER_MODEL: Optional[str] = None
    NOTES_MODEL: Optional[str] = None
    OCR_LANG: Optional[str] = None
    WHISPER_BACKEND: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    GROQ_WHISPER_MODEL: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_TRANSCRIBE_MODEL: Optional[str] = None

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
        bad = [s for s in v if s < 0 or s > MAX_PIPELINE_STAGE]
        if bad:
            raise ValueError(f"stages must be 0-{MAX_PIPELINE_STAGE}, got {bad}")
        return v


class ExamGenerateRequest(BaseModel):
    """POST /exams/generate body -- maps to
    scripts/11_generate_exam.py's --count/--questions/--force flags."""

    count: int = Field(default=1, ge=1, le=20)
    questions: Optional[int] = Field(default=None, ge=1)
    force: bool = False


class EssentialsGenerateRequest(BaseModel):
    """POST /essentials/generate body -- maps to scripts/09_lecture_
    essentials.py's and scripts/10_course_essentials.py's shared --force
    flag. Which lectures get a stage-9 task is derived server-side
    (webui/essentials.py::lectures_ready_for_essentials) from whichever
    already have finished notes, not taken from this body."""

    force: bool = False


class Correction(BaseModel):
    timestamp: float = Field(ge=0)
    slide_number: Optional[int] = None


class Corrections(BaseModel):
    """POST /review/{lecture_id} body."""

    corrections: list[Correction] = Field(min_length=1)
