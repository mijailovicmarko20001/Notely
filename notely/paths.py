"""Shared, stage-agnostic project paths, computed once.

Every stage script (00-08) and run_pipeline.py independently re-derived
PROJECT_ROOT as `Path(__file__).resolve().parent.parent`, and
webui/config.py did the same plus its own copies of the handful of shared
subdirectories below -- 11 near-identical computations in total. Since
notely/ lives alongside scripts/ and webui/ at the same project root, one
computation here (from *this* file's own location) gives every caller the
same answer.

Stage-specific output subdirectories (output/transcripts/,
output/slides_extracted/, etc.) are NOT here -- those vary per stage and
stay defined locally in each stage script, derived from OUTPUT_DIR below.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"
VIDEOS_DIR = INPUT_DIR / "videos"
SLIDES_DIR = INPUT_DIR / "slides"
VIDEO_URLS_PATH = INPUT_DIR / "video_urls.json"
