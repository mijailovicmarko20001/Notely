#!/usr/bin/env python3
"""
Orchestrator: Run pipeline stages in sequence

Executes stages 00-07 for one or more lectures, stopping with an error if
any stage exits nonzero.

Per-stage argv is built by notely.runner.build_tasks -- the same
orchestration logic the web UI's job scheduler uses (Phase 6) -- so this
CLI can pass stage 3/4/5's own tuning flags (--crop, --ocr-lang, --margin,
--min-dwell, etc.), not just --force.

Usage:
  python scripts/run_pipeline.py <lecture_id>          # Run stages 0-6
  python scripts/run_pipeline.py --all                  # Run stages 0-6 for all lectures
  python scripts/run_pipeline.py <lecture_id> --from 3 --to 5  # Run stages 3-5
  python scripts/run_pipeline.py --all --assemble       # Run all, then assemble final guide
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Only needed to bootstrap the `from notely...` import below (finding
# notely/ on sys.path) -- notely.paths.PROJECT_ROOT is the same value and
# is what get_project_root() below returns.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent

# notely/ (paths) lives alongside scripts/ and webui/ at the project root,
# not on sys.path by default when this file is run directly (python
# scripts/run_pipeline.py) -- same fix tests/conftest.py applies for test
# discovery. Must happen before the `from notely...` import below.
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.cli import require_lecture_id_or_all  # noqa: E402
from notely.io import load_json  # noqa: E402
from notely.paths import PROJECT_ROOT  # noqa: E402
from notely.runner import build_tasks  # noqa: E402
from notely.stages import MAX_PIPELINE_STAGE  # noqa: E402


def get_project_root():
    """Return the project root directory (parent of scripts/)."""
    return PROJECT_ROOT


def run_stage(stage_num, lecture_id=None, extra_args=None):
    """
    Run a single stage script.

    Args:
        stage_num: Stage number (0-7)
        lecture_id: Lecture ID (None for stage 7 which processes all)
        extra_args: List of extra command-line arguments to pass

    Returns:
        True if successful, False otherwise
    """
    project_root = get_project_root()

    # Find the actual script (glob to get the right one)
    scripts = list(project_root.glob(f"scripts/{stage_num:02d}_*.py"))
    if not scripts:
        print(f"Error: stage {stage_num} script not found", file=sys.stderr)
        return False

    stage_script = scripts[0]

    # Build command
    cmd = [sys.executable, str(stage_script)]
    if lecture_id:
        cmd.append(lecture_id)
    if extra_args:
        cmd.extend(extra_args)

    print(f"\n{'=' * 60}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'=' * 60}")

    result = subprocess.run(cmd, cwd=str(project_root))
    return result.returncode == 0


def get_all_lecture_ids(project_root):
    """Load lecture IDs from input/video_urls.json."""
    urls_path = project_root / "input" / "video_urls.json"
    try:
        urls_data = load_json(urls_path)
        return sorted(urls_data.keys())
    except FileNotFoundError:
        print(f"Error: {urls_path} not found", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Orchestrator: Run pipeline stages in sequence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_pipeline.py lecture01              # Run stages 0-6
  python scripts/run_pipeline.py --all                  # Run stages 0-6 for all
  python scripts/run_pipeline.py lecture01 --from 3 --to 5  # Run only stages 3-5
  python scripts/run_pipeline.py --all --assemble       # Run all, then assemble
  python scripts/run_pipeline.py lecture01 --ocr-lang eng --margin 0.2
        """,
    )
    parser.add_argument("lecture_id", nargs="?", help="Lecture ID (e.g., lecture01)")
    parser.add_argument("--all", action="store_true", help="Process all lectures from input/video_urls.json")
    parser.add_argument(
        "--from", type=int, dest="from_stage", default=0, help="Start from stage N (default: 0)"
    )
    parser.add_argument("--to", type=int, dest="to_stage", default=6, help="End at stage N (default: 6)")
    parser.add_argument(
        "--assemble", action="store_true", help="After processing all lectures, run stage 7 (assembly)"
    )
    parser.add_argument("--force", action="store_true", help="Pass --force to each stage script")

    # Per-stage tuning flags, forwarded via notely.runner.build_tasks
    # exactly like the web UI's job scheduler -- unset (None) means "let
    # that stage script use its own default" rather than baking a value in
    # here. Option keys match webui/config.py's DEFAULT_STAGE_OPTIONS.
    stage3 = parser.add_argument_group("stage 3 (detect slide changes)")
    stage3.add_argument("--crop", default=None, help='"x,y,w,h" fractions-of-frame to crop before comparison')
    stage3.add_argument("--threshold", type=float, default=None, help="slide-change diff threshold")
    stage3.add_argument("--interval", type=float, default=None, help="seconds between sampled frames")

    stage4 = parser.add_argument_group("stage 4 (match frames to slides)")
    stage4.add_argument(
        "--mode",
        choices=("deck", "visual"),
        default=None,
        help="'deck' (default) matches frames to slides; 'visual' segments by on-screen "
        "content for lectures that don't use the deck",
    )
    stage4.add_argument(
        "--auto-visual-threshold",
        type=float,
        default=None,
        help="deck mode: low-confidence share that triggers an automatic switch to visual (0 disables)",
    )
    stage4.add_argument(
        "--visual-threshold", type=float, default=None, help="visual mode: new-segment similarity cutoff"
    )
    stage4.add_argument(
        "--visual-min-seconds", type=float, default=None, help="visual mode: minimum segment length"
    )
    stage4.add_argument("--ocr-lang", default=None, help="tesseract language(s)")
    stage4.add_argument("--margin", type=float, default=None, help="backward-jump similarity margin")
    stage4.add_argument("--stay-margin", type=float, default=None, help="in-order stickiness margin")
    stage4.add_argument(
        "--confidence-threshold", type=float, default=None, help="needs_review flag threshold"
    )
    stage4.add_argument("--min-forward-score", type=float, default=None, help="forward-move score floor")
    stage4.add_argument(
        "--example-score-max", type=float, default=None, help="worked-example: whiteboard cutoff"
    )
    stage4.add_argument("--example-ink-delta", type=int, default=None, help="worked-example: dHash ink delta")
    stage4.add_argument(
        "--example-ink-text-overlap-min", type=float, default=None, help="worked-example: OCR overlap floor"
    )
    stage4.add_argument(
        "--example-ink-novel-word-min", type=float, default=None, help="worked-example: novel-word floor"
    )

    stage5 = parser.add_argument_group("stage 5 (segment transcript)")
    stage5.add_argument("--min-dwell", type=float, default=None, help="minimum slide dwell time in seconds")

    args = parser.parse_args()
    require_lecture_id_or_all(parser, args)

    if not (0 <= args.from_stage <= args.to_stage <= MAX_PIPELINE_STAGE):
        print(f"Error: invalid stage range --from {args.from_stage} --to {args.to_stage}", file=sys.stderr)
        sys.exit(1)

    project_root = get_project_root()

    if args.all:
        lecture_ids = get_all_lecture_ids(project_root)
        if lecture_ids is None:
            sys.exit(1)
    else:
        lecture_ids = [args.lecture_id]

    stages = list(range(args.from_stage, args.to_stage + 1))
    if args.assemble and MAX_PIPELINE_STAGE not in stages:
        stages.append(MAX_PIPELINE_STAGE)

    options = {
        "crop": args.crop,
        "threshold": args.threshold,
        "interval": args.interval,
        "mode": args.mode,
        "auto_visual_threshold": args.auto_visual_threshold,
        "visual_threshold": args.visual_threshold,
        "visual_min_seconds": args.visual_min_seconds,
        "ocr_lang": args.ocr_lang,
        "margin": args.margin,
        "stay_margin": args.stay_margin,
        "confidence_threshold": args.confidence_threshold,
        "min_forward_score": args.min_forward_score,
        "example_score_max": args.example_score_max,
        "example_ink_delta": args.example_ink_delta,
        "example_ink_text_overlap_min": args.example_ink_text_overlap_min,
        "example_ink_novel_word_min": args.example_ink_novel_word_min,
        "min_dwell": args.min_dwell,
    }
    # has_api_key=True unconditionally: unlike the web UI (which knows the
    # key's status ahead of time and uses it to lock stage 6 in the UI),
    # the CLI defers to stage 6's own script to detect and report a
    # missing ANTHROPIC_API_KEY, matching this entry point's existing
    # behavior of surfacing failures via each stage's own exit code.
    tasks = build_tasks(lecture_ids, stages, options, args.force, has_api_key=True)

    # Run tasks in order (grouped by lecture, per build_tasks). A failed
    # stage stops that lecture's remaining stages but not other lectures'
    # (matching D7/D6-era behavior); the trailing (None, ..., MAX_PIPELINE_STAGE)
    # assembly task is skipped if any lecture failed, and its own failure
    # exits immediately rather than folding into the per-lecture summary.
    failed = []  # [(lecture_id, stage), ...]
    failed_lectures = set()
    last_lecture_id = object()  # sentinel: never equal to a real lecture_id or None
    for lecture_id, stage, extra in tasks:
        if lecture_id is not None and lecture_id in failed_lectures:
            continue
        if lecture_id is None and failed_lectures:
            continue  # skip assembly: an earlier lecture failed

        if lecture_id != last_lecture_id:
            label = lecture_id if lecture_id is not None else "assembly"
            print(f"\n{'#' * 60}\n# Processing: {label}\n{'#' * 60}")
            last_lecture_id = lecture_id

        success = run_stage(stage, lecture_id, extra)
        if not success:
            if lecture_id is not None:
                print(f"Error: stage {stage} failed for {lecture_id}", file=sys.stderr)
                failed.append((lecture_id, stage))
                failed_lectures.add(lecture_id)
            else:
                print(f"Error: assembly (stage {stage}) failed", file=sys.stderr)
                sys.exit(1)

    # Summary
    if failed:
        print(f"\n{'!' * 60}")
        print(f"FAILED: {len(failed)} lecture(s)")
        for lecture_id, stage in failed:
            print(f"  {lecture_id}: stage {stage}")
        print(f"{'!' * 60}")
        sys.exit(1)
    else:
        print(f"\n{'=' * 60}")
        print("SUCCESS: All stages completed")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
