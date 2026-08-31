"""The stage registry: one record per pipeline stage, the single source
for stage names, script filenames, and per-lecture output artifact paths.

This information was hardcoded independently in webui/progress.py
(STAGE_NAMES + stage_artifact's table), webui/media.py (stage 8's script
filename), webui/models.py (the 0-7 stage-range validator),
webui/routes/jobs.py (range(7) for the per-lecture status dict), and
run_pipeline.py (the 0-7 --from/--to range) -- ~25 sites across those
files that had no way to stay in sync except by hand. Frontend
consumption (index.html/app.js) is deferred to Phase 7, per the cleanup
plan's own scoping.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# artifact_path takes (input_dir, output_dir, lecture_id) rather than
# closing over notely.paths' INPUT_DIR/OUTPUT_DIR directly, so callers that
# monkeypatch their own module-level copies of those constants for tests
# (e.g. webui/progress.py, re-exported from notely.paths) still see the
# path recomputed against the patched values -- see tests/test_progress.py.
ArtifactPathFn = Callable[[Path, Path, Optional[str]], Path]


@dataclass(frozen=True)
class Stage:
    number: int
    name: str
    script: str  # filename under scripts/, e.g. "04_match_frames_to_slides.py"
    per_lecture: bool  # False for course-level (7) or standalone (8) stages
    # The artifact whose existence+non-emptiness means this stage is done
    # (see webui/progress.py::artifact_ok). None for stages with no single
    # tracked artifact (8: caller picks the output path).
    artifact_path: Optional[ArtifactPathFn] = None


STAGES = [
    Stage(0, "Fetch video", "00_fetch_videos.py", True, lambda i, o, lec: i / "videos" / f"{lec}.mp4"),
    Stage(1, "Transcribe", "01_transcribe.py", True, lambda i, o, lec: o / "transcripts" / f"{lec}.json"),
    Stage(
        2,
        "Extract slides",
        "02_extract_slides.py",
        True,
        lambda i, o, lec: o / "slides_extracted" / f"{lec}.json",
    ),
    Stage(
        3,
        "Detect slide changes",
        "03_detect_slide_changes.py",
        True,
        lambda i, o, lec: o / "frame_events" / f"{lec}.json",
    ),
    Stage(
        4,
        "Match frames to slides",
        "04_match_frames_to_slides.py",
        True,
        lambda i, o, lec: o / "slide_timelines" / f"{lec}.json",
    ),
    Stage(
        5,
        "Segment transcript",
        "05_segment_transcript.py",
        True,
        lambda i, o, lec: o / "segmented_transcripts" / f"{lec}.json",
    ),
    Stage(6, "Generate notes", "06_generate_notes.py", True, lambda i, o, lec: o / "notes" / f"{lec}.md"),
    Stage(7, "Assemble study guide", "07_assemble.py", False, lambda i, o, lec: o / "study_guide.md"),
    Stage(8, "Export PDF", "08_export_pdf.py", False, None),
]

STAGES_BY_NUMBER = {s.number: s for s in STAGES}

# The highest stage run_pipeline.py / the job scheduler orchestrates as a
# sequence (0-7); stage 8 (PDF export) is invoked separately (webui/media.py,
# or scripts/08_export_pdf.py directly), never as part of that range.
MAX_PIPELINE_STAGE = 7

# Stages with a per-lecture artifact (0-6) -- what webui/routes/jobs.py's
# per-lecture "stages done" status dict iterates, as opposed to the
# course-level stage 7 or standalone stage 8.
PER_LECTURE_STAGES = [s.number for s in STAGES if s.per_lecture]
