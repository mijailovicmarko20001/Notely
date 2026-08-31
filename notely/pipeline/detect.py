"""Stage [3]: slide-change detection in video -- moved here from
scripts/03_detect_slide_changes.py (Phase 5), which is now a thin CLI
wrapper around process_lecture().

Samples video frames at a coarse interval, compares consecutive sampled
frames with a cheap similarity metric, and records a timestamp + saved
frame image every time the displayed slide appears to change. See
CLAUDE.md's "[3] Slide-change detection in video" for the full method.
"""

import sys
from pathlib import Path

from ..adapters.cv2_frame_reader import Cv2FrameReader
from ..io import save_json
from ..paths import PROJECT_ROOT
from ..paths import VIDEOS_DIR as INPUT_VIDEOS_DIR

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
    frame_reader=None,
):
    """
    Sample `video_path` every `interval` seconds, detect slide-change events,
    and save each event's frame as PNG into `frames_dir`.

    Returns a list of {"timestamp": float, "frame_image_path": str} dicts,
    with paths relative to PROJECT_ROOT.

    frame_reader: a FrameReader (see notely.ports), defaults to the real
    cv2-backed adapter; tests inject a fake instead of needing a real video
    file (or opencv installed at all)."""
    import cv2  # still needed here for imwrite -- the frame-sampling/
    # stepping itself is the FrameReader port's job, not this function's

    if frame_reader is None:
        frame_reader = Cv2FrameReader()

    frames_dir.mkdir(parents=True, exist_ok=True)

    events = []
    prev_gray = None
    event_index = 0

    for sampled in frame_reader.sample_frames(video_path, interval):
        cropped = apply_crop(sampled.frame, crop)
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
                    "timestamp": round(sampled.timestamp, 3),
                    "frame_image_path": str(image_path.relative_to(PROJECT_ROOT)),
                }
            )
            tag = "t=0" if is_first_frame else f"diff={score:.3f}"
            print(f"  [event {event_index:03d}] t={sampled.timestamp:8.2f}s ({tag}) -> {image_name}")
            event_index += 1

        prev_gray = gray

    return events


def process_lecture(
    lecture_id: str,
    interval: float,
    threshold: float,
    crop_str: str | None,
    force: bool,
    frame_reader=None,
) -> bool:
    """Returns False only when no video was found (the caller should treat
    that as a failure); an already-done skip and a real successful run
    both return True.

    frame_reader: a FrameReader (see notely.ports), defaults to the real
    cv2-backed adapter; tests inject a fake instead of needing a real
    video file."""
    video_path = INPUT_VIDEOS_DIR / f"{lecture_id}.mp4"
    output_json = OUTPUT_DIR / f"{lecture_id}.json"
    frames_dir = OUTPUT_DIR / f"{lecture_id}_frames"

    if not video_path.exists():
        print(f"[skip] {lecture_id}: no video found at {video_path}", file=sys.stderr)
        return False

    if output_json.exists() and not force:
        print(f"[skip] {lecture_id}: {output_json} already exists (use --force to redo)")
        return True

    crop = parse_crop(crop_str) if crop_str else None

    print(f"[{lecture_id}] detecting slide changes: interval={interval}s threshold={threshold} crop={crop}")
    events = detect_events(video_path, interval, threshold, crop, frames_dir, frame_reader=frame_reader)

    # Atomic write (see notely.io): a killed process can never leave a
    # truncated-but-non-empty artifact that a later run's exists()-and-
    # nonempty skip check would wrongly trust as done.
    save_json(output_json, events)

    print(f"[done] {lecture_id}: {len(events)} frame events -> {output_json}")
    return True
