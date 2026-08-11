#!/usr/bin/env python3
"""
Stage [3]: Slide-change detection in video
============================================

Samples video frames at a coarse interval, compares consecutive sampled
frames with a cheap similarity metric, and records a timestamp + saved
frame image every time the displayed slide appears to change.

Method (per CLAUDE.md "[3] Slide-change detection in video"):
  1. Sample frames every ~1.5s (configurable via --interval) with OpenCV.
  2. Optionally crop each frame to a screen-share region first (--crop
     "x,y,w,h" as fractions of frame width/height), for recordings with a
     webcam picture-in-picture overlay.
  3. Downscale to ~320px wide, convert to grayscale, and compute the
     normalized mean absolute pixel difference between consecutive sampled
     frames. A diff above --threshold (default 0.08) = slide change event.
  4. The very first sampled frame (t=0) is always saved as event_000,
     regardless of whether a "change" was detected for it, since it is the
     slide showing at the start of the video.
  5. Every change event's frame is saved as a PNG, and its timestamp +
     image path recorded.

This stage intentionally produces noisy output (cursor movement, slide
animations, and video artifacts can trigger false positives) — stage [4]
absorbs that noise via OCR + text-similarity + the sequential-order
constraint, and consecutive events resolving to the same slide get
collapsed there.

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

Notes:
    - cv2 (opencv-python) is imported lazily inside main-path functions so
      that `python -m py_compile` and `--help` work without the dependency
      installed.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Project root = parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_VIDEOS_DIR = PROJECT_ROOT / "input" / "videos"
OUTPUT_DIR = PROJECT_ROOT / "output" / "frame_events"

# Frames are downscaled to this width before diffing, for speed + to reduce
# sensitivity to fine-grained noise (compression artifacts, cursor blink).
DIFF_WIDTH = 320


def parse_crop(crop_str: str) -> tuple[float, float, float, float]:
    """Parse a "x,y,w,h" fraction-of-frame string into a 4-tuple of floats."""
    parts = [p.strip() for p in crop_str.split(",")]
    if len(parts) != 4:
        raise ValueError(f"--crop must be 'x,y,w,h', got: {crop_str!r}")
    x, y, w, h = (float(p) for p in parts)
    for name, val in (("x", x), ("y", y), ("w", w), ("h", h)):
        if not (0.0 <= val <= 1.0):
            raise ValueError(f"--crop component {name}={val} must be a fraction in [0, 1]")
    if x + w > 1.0 + 1e-9 or y + h > 1.0 + 1e-9:
        raise ValueError(f"--crop region extends past frame bounds: {crop_str!r}")
    return x, y, w, h


def apply_crop(frame, crop: tuple[float, float, float, float] | None):
    """Crop a BGR frame to the given fractional (x, y, w, h) region, if any."""
    if crop is None:
        return frame
    height, width = frame.shape[:2]
    x, y, w, h = crop
    x0, y0 = int(round(x * width)), int(round(y * height))
    x1, y1 = int(round((x + w) * width)), int(round((y + h) * height))
    # Guard against degenerate rounding producing an empty slice.
    x1, y1 = max(x1, x0 + 1), max(y1, y0 + 1)
    return frame[y0:y1, x0:x1]


def preprocess_for_diff(frame):
    """Downscale to ~DIFF_WIDTH px wide grayscale, for cheap frame comparison."""
    import cv2

    height, width = frame.shape[:2]
    scale = DIFF_WIDTH / width
    resized = cv2.resize(
        frame, (DIFF_WIDTH, max(1, int(round(height * scale)))), interpolation=cv2.INTER_AREA
    )
    return cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)


def frame_diff_score(prev_gray, curr_gray) -> float:
    """
    Similarity metric: normalized mean absolute pixel difference between two
    preprocessed (downscaled grayscale) frames, in [0, 1].

    This is a standalone function precisely so the metric is swappable
    (e.g. for SSIM) without touching the sampling/event-detection loop.
    """
    import numpy as np

    diff = np.abs(curr_gray.astype("int16") - prev_gray.astype("int16"))
    return float(diff.mean()) / 255.0


def detect_events(
    video_path: Path,
    interval: float,
    threshold: float,
    crop: tuple[float, float, float, float] | None,
    frames_dir: Path,
):
    """
    Sample `video_path` every `interval` seconds, detect slide-change events,
    and save each event's frame as PNG into `frames_dir`.

    Returns a list of {"timestamp": float, "frame_image_path": str} dicts,
    with paths relative to PROJECT_ROOT.
    """
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 0:
        cap.release()
        raise RuntimeError(f"could not read FPS for video: {video_path} (fps={fps})")

    frame_step = max(1, int(round(interval * fps)))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    frames_dir.mkdir(parents=True, exist_ok=True)

    events = []
    prev_gray = None
    event_index = 0
    frame_idx = 0

    while True:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok:
            break

        timestamp = frame_idx / fps
        cropped = apply_crop(frame, crop)
        gray = preprocess_for_diff(cropped)

        is_first_frame = prev_gray is None
        is_change = False
        score = 0.0
        if not is_first_frame:
            score = frame_diff_score(prev_gray, gray)
            is_change = score > threshold

        # Always save the very first sampled frame (t=0 slide), plus every
        # detected change event.
        if is_first_frame or is_change:
            image_name = f"event_{event_index:03d}.png"
            image_path = frames_dir / image_name
            cv2.imwrite(str(image_path), cropped)
            events.append(
                {
                    "timestamp": round(timestamp, 3),
                    "frame_image_path": str(image_path.relative_to(PROJECT_ROOT)),
                }
            )
            tag = "t=0" if is_first_frame else f"diff={score:.3f}"
            print(f"  [event {event_index:03d}] t={timestamp:8.2f}s ({tag}) -> {image_name}")
            event_index += 1

        prev_gray = gray
        frame_idx += frame_step
        if total_frames and frame_idx >= total_frames:
            break

    cap.release()
    return events


def process_lecture(
    lecture_id: str,
    interval: float,
    threshold: float,
    crop_str: str | None,
    force: bool,
) -> None:
    video_path = INPUT_VIDEOS_DIR / f"{lecture_id}.mp4"
    output_json = OUTPUT_DIR / f"{lecture_id}.json"
    frames_dir = OUTPUT_DIR / f"{lecture_id}_frames"

    if not video_path.exists():
        print(f"[skip] {lecture_id}: no video found at {video_path}", file=sys.stderr)
        return

    if output_json.exists() and not force:
        print(f"[skip] {lecture_id}: {output_json} already exists (use --force to redo)")
        return

    crop = parse_crop(crop_str) if crop_str else None

    print(f"[{lecture_id}] detecting slide changes: interval={interval}s threshold={threshold} crop={crop}")
    events = detect_events(video_path, interval, threshold, crop, frames_dir)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Temp file + atomic rename: a killed process can never leave a
    # truncated-but-non-empty artifact that a later run's exists()-and-
    # nonempty skip check would wrongly trust as done.
    tmp_json = output_json.with_name(f"{output_json.name}.tmp{os.getpid()}")
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2)
    tmp_json.replace(output_json)

    print(f"[done] {lecture_id}: {len(events)} frame events -> {output_json}")


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
        "--interval", type=float, default=1.5, help="seconds between sampled frames (default: 1.5)"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.08,
        help="normalized mean-abs-diff above which a frame pair counts as a slide change (default: 0.08)",
    )
    parser.add_argument(
        "--crop",
        default=None,
        help='optional "x,y,w,h" fractions-of-frame to crop to the screen-share region before comparison',
    )
    args = parser.parse_args()

    if bool(args.all) == bool(args.lecture_id):
        parser.error("provide exactly one of <lecture_id> or --all")

    if args.all:
        video_files = sorted(INPUT_VIDEOS_DIR.glob("*.mp4"))
        if not video_files:
            print(f"No videos found in {INPUT_VIDEOS_DIR}")
            return
        lecture_ids = [p.stem for p in video_files]
    else:
        lecture_ids = [args.lecture_id]

    for lecture_id in lecture_ids:
        process_lecture(
            lecture_id,
            interval=args.interval,
            threshold=args.threshold,
            crop_str=args.crop,
            force=args.force,
        )


if __name__ == "__main__":
    main()
