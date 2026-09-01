#!/usr/bin/env python3
"""
Stage [3]: Slide-change detection in video
============================================

Samples video frames at a coarse interval, compares consecutive sampled
frames with a cheap similarity metric, and records a timestamp + saved
frame image every time the displayed slide appears to change.

The actual detection logic lives in notely.pipeline.detect (Phase 5) --
this script is just the CLI wrapper around it. See CLAUDE.md's "[3]
Slide-change detection in video" for the full method.

Usage:
    python scripts/03_detect_slide_changes.py <lecture_id>
    python scripts/03_detect_slide_changes.py --all
    python scripts/03_detect_slide_changes.py <lecture_id> --force
    python scripts/03_detect_slide_changes.py <lecture_id> --interval 2.0 --threshold 0.06
    python scripts/03_detect_slide_changes.py <lecture_id> --crop "0.0,0.0,0.75,1.0"

Output:
    output/frame_events/<lecture_id>.json
        [{"timestamp": float_seconds, "frame_image_path": str}, ...]
    output/frame_events/<lecture_id>_frames/event_NNN.png
"""

import argparse
import sys
from pathlib import Path

# Only needed to bootstrap the `from notely...` import below (finding
# notely/ on sys.path) -- notely.paths.PROJECT_ROOT is the same value and
# is what the rest of this file uses.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent

# notely/ (ports, adapters, paths) lives alongside scripts/ and webui/ at
# the project root, not on sys.path by default when this file is run
# directly (python scripts/03_detect_slide_changes.py) -- same fix
# tests/conftest.py applies for test discovery. Must happen before the
# `from notely...` import below.
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.cli import require_lecture_id_or_all  # noqa: E402
from notely.env import DEFAULT_STAGE3_THRESHOLD  # noqa: E402
from notely.pipeline.detect import (  # noqa: E402
    INPUT_VIDEOS_DIR,
    OUTPUT_DIR,
    PROJECT_ROOT,
    apply_crop,
    detect_events,
    frame_diff_score,
    parse_crop,
    preprocess_for_diff,
    process_lecture,
)

__all__ = [
    "INPUT_VIDEOS_DIR",
    "OUTPUT_DIR",
    "PROJECT_ROOT",
    "apply_crop",
    "detect_events",
    "frame_diff_score",
    "parse_crop",
    "preprocess_for_diff",
    "process_lecture",
    "main",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage [3]: detect slide-change events by sampling and diffing video frames."
    )
    parser.add_argument(
        "lecture_id",
        nargs="?",
        default=None,
        help="Lecture id, e.g. lecture01 (matches input/videos/<lecture_id>.mp4)",
    )
    parser.add_argument("--all", action="store_true", help="process every video found in input/videos/")
    parser.add_argument(
        "--force", action="store_true", help="re-run even if output/frame_events/<lecture_id>.json exists"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.5,
        help=(
            "seconds between sampled frames (default: 1.5, sized so a quick slide "
            "flip isn't missed). If you intend to run stage 4 with --mode visual, "
            "pass a larger value (~4): that mode won't emit a segment shorter than "
            "45s anyway, so 1.5s oversamples by ~3x and stage 4's OCR pays for every "
            "extra frame"
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_STAGE3_THRESHOLD,
        help=(
            "normalized mean-abs-diff above which a frame pair counts as a slide change "
            "(default: 0.02 -- see DOCUMENTATION.md's calibration table; 0.08 detected "
            "almost nothing on real course recordings)"
        ),
    )
    parser.add_argument(
        "--crop",
        default=None,
        help='optional "x,y,w,h" fractions-of-frame to crop to the screen-share region before comparison',
    )
    args = parser.parse_args()

    require_lecture_id_or_all(parser, args)

    if args.all:
        video_files = sorted(INPUT_VIDEOS_DIR.glob("*.mp4"))
        if not video_files:
            print(f"No videos found in {INPUT_VIDEOS_DIR}")
            return
        lecture_ids = [p.stem for p in video_files]
    else:
        lecture_ids = [args.lecture_id]

    failures = []
    for lecture_id in lecture_ids:
        ok = process_lecture(
            lecture_id,
            interval=args.interval,
            threshold=args.threshold,
            crop_str=args.crop,
            force=args.force,
        )
        if not ok:
            failures.append(lecture_id)

    if failures:
        print(f"FAILED: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
