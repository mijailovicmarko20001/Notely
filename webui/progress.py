"""Stage stdout parsing and stage success criteria.

Stages 01-04 exit 0 even when they skip for missing input, so exit codes
alone can't tell us whether a stage produced anything: success is judged by
the expected artifact existing on disk (STAGE_ARTIFACTS).
"""

import re

from notely.stages import STAGES, STAGES_BY_NUMBER
from . import config

# Derived from the full registry (not just range(MAX_PIPELINE_STAGE + 1))
# so standalone stages invoked outside the orchestrated 0-7 sweep --
# stage 8 (PDF export, never job-scheduled) and stage 11 (practice exams,
# which IS job-scheduled -- see webui/routes/exams.py building its own
# task list for MANAGER.start_job) -- get a name too. start_job() would
# otherwise KeyError building its task snapshot the moment a stage-11
# task reached it.
STAGE_NAMES = {s.number: s.name for s in STAGES}


def stage_artifact(stage: int, lecture_id: str = None):
    """Path that must exist (and be non-empty) for the stage to count as done.

    Looks up config.INPUT_DIR/OUTPUT_DIR through the module reference (not
    a `from .config import` copy) so tests can monkeypatch
    config.INPUT_DIR/OUTPUT_DIR to redirect artifact checks into a tmp
    tree -- one place to patch instead of a separate copy per importer."""
    return STAGES_BY_NUMBER[stage].artifact_path(config.INPUT_DIR, config.OUTPUT_DIR, lecture_id)


def artifact_ok(stage: int, lecture_id: str = None) -> bool:
    p = stage_artifact(stage, lecture_id)
    return p.exists() and p.stat().st_size > 0


# --- per-stage line -> percent parsers ------------------------------------
# Each returns a float in [0, 1] or None. ctx carries {"video_duration": float|None}.

_RE_YTDLP = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
_RE_WHISPER_SEG = re.compile(r"\[\s*([\d.]+)\s*->\s*([\d.]+)\s*\]")
# mlx-whisper verbose format: "[MM:SS.mmm --> MM:SS.mmm]" (H:MM:SS past 1h)
_RE_MLX_SEG = re.compile(r"-->\s*(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)\]")
_RE_EVENT = re.compile(r"\[event\s+\d+\]\s+t=\s*([\d.]+)s")
_RE_OCR = re.compile(r"\[ocr\s+(\d+)/(\d+)\]")
_RE_NOTES = re.compile(r"\[(\d+)/(\d+)\]\s+slide")
# Worked-example confirmation phase (opt-in, NOTES_DETECT_EXAMPLES) runs
# before the per-slide notes phase within stage 6 and shares its "[N/M] ..."
# progress-line shape but a different noun -- see
# scripts/06_generate_notes.py::confirm_example.
_RE_EXAMPLE = re.compile(r"\[(\d+)/(\d+)\]\s+example")
# Stage 9 (notely/pipeline/essentials.py::process_lecture_essentials) --
# not currently reachable via the web UI (stage 9 is standalone, outside
# MAX_PIPELINE_STAGE), but kept here so a future wiring gets a progress
# bar for free instead of the indeterminate spinner stages 2/5/7 get.
_RE_ESSENTIALS = re.compile(r"\[(\d+)/(\d+)\]\s+lecture")
# Stage 11 (notely/pipeline/exams.py::generate_one_exam) -- reachable via
# the web UI (webui/routes/exams.py starts it through the job scheduler,
# unlike stages 9/10).
_RE_EXAM = re.compile(r"\[(\d+)/(\d+)\]\s+exam")


def parse_line(stage: int, line: str, ctx: dict):
    duration = ctx.get("video_duration")
    if stage == 0:
        m = _RE_YTDLP.search(line)
        if m:
            return float(m.group(1)) / 100.0
    elif stage == 1 and duration:
        m = _RE_WHISPER_SEG.search(line)
        if m:
            return min(float(m.group(2)) / duration, 1.0)
        m = _RE_MLX_SEG.search(line)
        if m:
            h, mins, secs = m.groups()
            end = int(h or 0) * 3600 + int(mins) * 60 + float(secs)
            return min(end / duration, 1.0)
    elif stage == 3 and duration:
        m = _RE_EVENT.search(line)
        if m:
            return min(float(m.group(1)) / duration, 1.0)
    elif stage == 4:
        m = _RE_OCR.search(line)
        if m:
            return int(m.group(1)) / int(m.group(2))
    elif stage == 6:
        m = _RE_NOTES.search(line) or _RE_EXAMPLE.search(line)
        if m:
            return int(m.group(1)) / int(m.group(2))
    elif stage == 9:
        m = _RE_ESSENTIALS.search(line)
        if m:
            return int(m.group(1)) / int(m.group(2))
    elif stage == 11:
        m = _RE_EXAM.search(line)
        if m:
            return int(m.group(1)) / int(m.group(2))
    return None  # stages 2/5/7 are quick: indeterminate spinner
