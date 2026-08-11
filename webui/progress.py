"""Stage stdout parsing and stage success criteria.

Stages 01-04 exit 0 even when they skip for missing input, so exit codes
alone can't tell us whether a stage produced anything: success is judged by
the expected artifact existing on disk (STAGE_ARTIFACTS).
"""

import re

from .config import INPUT_DIR, OUTPUT_DIR

STAGE_NAMES = {
    0: "Fetch video",
    1: "Transcribe",
    2: "Extract slides",
    3: "Detect slide changes",
    4: "Match frames to slides",
    5: "Segment transcript",
    6: "Generate notes",
    7: "Assemble study guide",
}


def stage_artifact(stage: int, lecture_id: str = None):
    """Path that must exist (and be non-empty) for the stage to count as done."""
    table = {
        0: INPUT_DIR / "videos" / f"{lecture_id}.mp4",
        1: OUTPUT_DIR / "transcripts" / f"{lecture_id}.json",
        2: OUTPUT_DIR / "slides_extracted" / f"{lecture_id}.json",
        3: OUTPUT_DIR / "frame_events" / f"{lecture_id}.json",
        4: OUTPUT_DIR / "slide_timelines" / f"{lecture_id}.json",
        5: OUTPUT_DIR / "segmented_transcripts" / f"{lecture_id}.json",
        6: OUTPUT_DIR / "notes" / f"{lecture_id}.md",
        7: OUTPUT_DIR / "study_guide.md",
    }
    return table[stage]


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
        m = _RE_NOTES.search(line)
        if m:
            return int(m.group(1)) / int(m.group(2))
    return None  # stages 2/5/7 are quick: indeterminate spinner
