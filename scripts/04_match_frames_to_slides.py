#!/usr/bin/env python3
"""
Stage [4]: Frame-to-slide matching
=====================================

For each frame-change event detected in stage [3], figure out which slide
number it actually is, using OCR + TF-IDF text similarity constrained by the
assumption that slides are shown roughly in sequential order.

The actual matching logic lives in notely.pipeline.matching, and
worked-example candidate detection in notely.pipeline.example_detect
(Phase 5) -- this script is just the CLI wrapper around them. See
CLAUDE.md's "[4] Frame-to-slide matching" for the full method.

Usage:
    python scripts/04_match_frames_to_slides.py <lecture_id>
    python scripts/04_match_frames_to_slides.py --all
    python scripts/04_match_frames_to_slides.py <lecture_id> --force
    python scripts/04_match_frames_to_slides.py <lecture_id> --margin 0.2 --confidence-threshold 0.3

Inputs:
    output/frame_events/<lecture_id>.json
        [{"timestamp": float, "frame_image_path": str}, ...]
    output/slides_extracted/<lecture_id>.json
        [{"slide_number": int, "title": str, "body_text": str,
          "notes_text": str, "image_path": str}, ...]

Outputs:
    output/slide_timelines/<lecture_id>.json
        {"timeline": [{"slide_number": int, "start": float,
                        "end": float | null, "confidence": float}, ...],
         "notes": [str, ...]}
    output/slide_timelines/<lecture_id>_needs_review.json
        {"low_confidence_matches": [...], "unmatched_slides": [...],
         "backward_jumps": [...]}
    output/slide_timelines/<lecture_id>_examples.json
        {"candidates": [{"event_index": int, "timestamp": float,
                          "frame_image_path": str, "slide_number": int,
                          "kind": "whiteboard"|"annotated_slide"|"example_slide",
                          "score": float, "ink_delta": int | null,
                          "ocr_excerpt": str}, ...]}
        Frame events that look like the professor working a worked example --
        off-deck (whiteboard/tablet), annotated live over a slide, or the
        deck's own example slide. Always computed (free, local heuristics);
        confirmation/captioning by an LLM happens downstream in stage 6, gated
        behind NOTES_DETECT_EXAMPLES. See CLAUDE.md for the full feature.

Notes:
    - pytesseract and sklearn are imported lazily inside the functions that
      need them, so `python -m py_compile` and `--help` work without those
      dependencies installed.
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
# directly (python scripts/04_match_frames_to_slides.py) -- same fix
# tests/conftest.py applies for test discovery. Must happen before the
# `from notely...` import below.
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.cli import require_lecture_id_or_all  # noqa: E402
from notely.env import DEFAULT_OCR_LANG, env_str  # noqa: E402
from notely.paths import PROJECT_ROOT  # noqa: E402
from notely.pipeline.example_detect import (  # noqa: E402
    DEFAULT_EXAMPLE_INK_DELTA,
    DEFAULT_EXAMPLE_INK_NOVEL_WORD_MIN,
    DEFAULT_EXAMPLE_INK_TEXT_OVERLAP_MIN,
    DEFAULT_EXAMPLE_SCORE_MAX,
    DHASH_DEDUP_THRESHOLD,
    OCR_EXCERPT_LEN,
    detect_example_candidates,
    frame_hash,
    group_runs,
    hamming_distance,
    ocr_novel_word_ratio,
    ocr_text_overlap,
)
from notely.pipeline.matching import (  # noqa: E402
    DEFAULT_AUTO_VISUAL_THRESHOLD,
    DEFAULT_BACKWARD_JUMP_MARGIN,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_MIN_FORWARD_SCORE,
    DEFAULT_STAY_MARGIN,
    FRAME_EVENTS_DIR,
    MODE_DECK,
    MODE_VISUAL,
    MODES,
    SLIDES_EXTRACTED_DIR,
    OUTPUT_DIR,
    build_needs_review,
    build_slide_reference_texts,
    collapse_to_timeline,
    compute_similarity_matrix,
    match_events_to_slides,
    process_lecture,
)
from notely.pipeline.visual_segment import (  # noqa: E402
    DEFAULT_MIN_SEGMENT_SECONDS,
    DEFAULT_SIMILARITY_THRESHOLD,
    build_visual_timeline,
)

# Best-effort .env loading (same pattern as stages 00/01/06), so OCR_LANG
# set in .env actually reaches the --ocr-lang default below.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

__all__ = [
    "DEFAULT_AUTO_VISUAL_THRESHOLD",
    "DEFAULT_BACKWARD_JUMP_MARGIN",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_MIN_FORWARD_SCORE",
    "DEFAULT_STAY_MARGIN",
    "FRAME_EVENTS_DIR",
    "SLIDES_EXTRACTED_DIR",
    "OUTPUT_DIR",
    "build_needs_review",
    "build_slide_reference_texts",
    "collapse_to_timeline",
    "compute_similarity_matrix",
    "match_events_to_slides",
    "process_lecture",
    "DEFAULT_EXAMPLE_INK_DELTA",
    "DEFAULT_EXAMPLE_INK_NOVEL_WORD_MIN",
    "DEFAULT_EXAMPLE_INK_TEXT_OVERLAP_MIN",
    "DEFAULT_EXAMPLE_SCORE_MAX",
    "DHASH_DEDUP_THRESHOLD",
    "OCR_EXCERPT_LEN",
    "detect_example_candidates",
    "frame_hash",
    "group_runs",
    "hamming_distance",
    "ocr_novel_word_ratio",
    "ocr_text_overlap",
    "MODE_DECK",
    "MODE_VISUAL",
    "MODES",
    "DEFAULT_MIN_SEGMENT_SECONDS",
    "DEFAULT_SIMILARITY_THRESHOLD",
    "build_visual_timeline",
    "main",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage [4]: match frame-change events to slide numbers via OCR + TF-IDF."
    )
    parser.add_argument(
        "lecture_id",
        nargs="?",
        default=None,
        help="Lecture id, e.g. lecture01 (matches output/frame_events/<lecture_id>.json)",
    )
    parser.add_argument(
        "--all", action="store_true", help="process every lecture found in output/frame_events/"
    )
    parser.add_argument(
        "--force", action="store_true", help="re-run even if output/slide_timelines/<lecture_id>.json exists"
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=DEFAULT_BACKWARD_JUMP_MARGIN,
        help=f"similarity margin required to accept a backward jump (default: {DEFAULT_BACKWARD_JUMP_MARGIN})",
    )
    parser.add_argument(
        "--stay-margin",
        type=float,
        default=DEFAULT_STAY_MARGIN,
        help=(
            "similarity margin a competing in-order candidate must beat the current "
            f"slide's own score by before the cursor advances (default: {DEFAULT_STAY_MARGIN}). "
            "Curbs oscillation between near-duplicate consecutive slides."
        ),
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        help=f"matches below this score are flagged in needs_review.json (default: {DEFAULT_CONFIDENCE_THRESHOLD})",
    )
    parser.add_argument(
        "--min-forward-score",
        type=float,
        default=DEFAULT_MIN_FORWARD_SCORE,
        help=(
            "absolute score floor an in-order candidate must clear to move the cursor "
            f"off the current slide (default: {DEFAULT_MIN_FORWARD_SCORE}). Set to 0 for "
            "the old stay_margin-only behavior."
        ),
    )
    parser.add_argument(
        "--ocr-lang",
        default=env_str("OCR_LANG", DEFAULT_OCR_LANG),
        help='tesseract language(s) (default: env OCR_LANG or "srp_latn+eng", matching webui/config.py)',
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=MODE_DECK,
        help=(
            f"how to build the timeline. '{MODE_DECK}' (default) matches each frame to a "
            f"slide number in the deck. '{MODE_VISUAL}' ignores the deck entirely and "
            "segments the lecture by what's on screen -- for recordings that don't "
            "present slides, where deck mode silently collapses the whole lecture into "
            "one or two runs"
        ),
    )
    parser.add_argument(
        "--auto-visual-threshold",
        type=float,
        default=DEFAULT_AUTO_VISUAL_THRESHOLD,
        help=(
            "--mode deck only: if this fraction or more of the matches score below "
            f"--confidence-threshold, the deck is judged not to be on screen and the "
            f"timeline is rebuilt by visual segmentation instead (default: "
            f"{DEFAULT_AUTO_VISUAL_THRESHOLD}). The switch is printed and recorded in "
            "the timeline's notes. Set to 0 to disable and always keep the slide matching"
        ),
    )
    parser.add_argument(
        "--visual-threshold",
        type=float,
        default=DEFAULT_SIMILARITY_THRESHOLD,
        help=(
            "--mode visual only: cosine similarity between consecutive frames' OCR text "
            f"below which a new segment starts (default: {DEFAULT_SIMILARITY_THRESHOLD}). "
            "Lower it for fewer, longer segments; raise it for more, shorter ones"
        ),
    )
    parser.add_argument(
        "--visual-min-seconds",
        type=float,
        default=DEFAULT_MIN_SEGMENT_SECONDS,
        help=(
            "--mode visual only: segments shorter than this are merged into the previous "
            f"one, absorbing single-frame OCR noise (default: {DEFAULT_MIN_SEGMENT_SECONDS})"
        ),
    )
    parser.add_argument(
        "--no-examples",
        action="store_true",
        help="skip worked-example candidate detection (writes an empty _examples.json)",
    )
    parser.add_argument(
        "--example-score-max",
        type=float,
        default=DEFAULT_EXAMPLE_SCORE_MAX,
        help=(
            "match score below which a frame is flagged as a 'whiteboard' example "
            f"candidate (default: {DEFAULT_EXAMPLE_SCORE_MAX})"
        ),
    )
    parser.add_argument(
        "--example-ink-delta",
        type=int,
        default=DEFAULT_EXAMPLE_INK_DELTA,
        help=(
            "dHash Hamming distance from a run's first frame above which a frame is "
            f"flagged as an 'annotated_slide' example candidate (default: {DEFAULT_EXAMPLE_INK_DELTA})"
        ),
    )
    parser.add_argument(
        "--example-ink-text-overlap-min",
        type=float,
        default=DEFAULT_EXAMPLE_INK_TEXT_OVERLAP_MIN,
        help=(
            "minimum fraction of a run's first frame's OCR words that must still appear "
            "in a candidate frame's OCR text before dHash drift counts as 'annotated_slide' "
            f"rather than a different, misgrouped slide (default: {DEFAULT_EXAMPLE_INK_TEXT_OVERLAP_MIN})"
        ),
    )
    parser.add_argument(
        "--example-ink-novel-word-min",
        type=float,
        default=DEFAULT_EXAMPLE_INK_NOVEL_WORD_MIN,
        help=(
            "minimum fraction of a candidate frame's OCR words that must be ABSENT from "
            "the deck's own extracted text for that slide before dHash drift counts as "
            "'annotated_slide' rather than a PowerPoint animation build revealing more of "
            f"the deck's own printed content (default: {DEFAULT_EXAMPLE_INK_NOVEL_WORD_MIN})"
        ),
    )
    args = parser.parse_args()

    require_lecture_id_or_all(parser, args)

    if args.all:
        event_files = sorted(FRAME_EVENTS_DIR.glob("*.json"))
        # exclude nothing special here since frame_events only ever contains
        # <lecture_id>.json files (frame PNGs live in a sibling _frames/ dir)
        lecture_ids = [p.stem for p in event_files]
        if not lecture_ids:
            print(f"No frame event files found in {FRAME_EVENTS_DIR}")
            return
    else:
        lecture_ids = [args.lecture_id]

    failures = []
    for lecture_id in lecture_ids:
        ok = process_lecture(
            lecture_id,
            margin=args.margin,
            confidence_threshold=args.confidence_threshold,
            force=args.force,
            ocr_lang=args.ocr_lang,
            stay_margin=args.stay_margin,
            min_forward_score=args.min_forward_score,
            detect_examples=not args.no_examples,
            example_score_max=args.example_score_max,
            example_ink_delta=args.example_ink_delta,
            example_ink_text_overlap_min=args.example_ink_text_overlap_min,
            example_ink_novel_word_min=args.example_ink_novel_word_min,
            mode=args.mode,
            auto_visual_threshold=args.auto_visual_threshold,
            visual_threshold=args.visual_threshold,
            visual_min_seconds=args.visual_min_seconds,
        )
        if not ok:
            failures.append(lecture_id)

    if failures:
        print(f"FAILED: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
