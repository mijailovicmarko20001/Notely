#!/usr/bin/env python3
"""
Orchestrator: Run pipeline stages in sequence

Executes stages 00-07 for one or more lectures, stopping with an error if
any stage exits nonzero.

Usage:
  python scripts/run_pipeline.py <lecture_id>          # Run stages 0-6 for one lecture
  python scripts/run_pipeline.py --all                  # Run stages 0-6 for all lectures
  python scripts/run_pipeline.py <lecture_id> --from 3 --to 5  # Run stages 3-5
  python scripts/run_pipeline.py --all --assemble       # Run all, then assemble final guide
"""

import json
import sys
import subprocess
import argparse
from pathlib import Path


def get_project_root():
    """Return the project root directory (parent of scripts/)."""
    return Path(__file__).parent.parent


def load_json(path):
    """Load JSON from file."""
    with open(path) as f:
        return json.load(f)


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
    parser.add_argument("--min-dwell", type=float, help="Pass --min-dwell to stage 5 (segment_transcript)")

    args = parser.parse_args()

    project_root = get_project_root()

    # Determine lecture list
    if args.all:
        lecture_ids = get_all_lecture_ids(project_root)
        if lecture_ids is None:
            sys.exit(1)
    else:
        if not args.lecture_id:
            parser.print_help()
            sys.exit(1)
        lecture_ids = [args.lecture_id]

    # Validate stage range
    if not (0 <= args.from_stage <= args.to_stage <= 7):
        print(f"Error: invalid stage range --from {args.from_stage} --to {args.to_stage}", file=sys.stderr)
        sys.exit(1)

    # Build extra args to pass to stage scripts
    extra_args = []
    if args.force:
        extra_args.append("--force")
    if args.min_dwell is not None:
        extra_args.extend(["--min-dwell", str(args.min_dwell)])

    # Run stages for each lecture
    failed_lectures = []
    for lecture_id in lecture_ids:
        print(f"\n{'#' * 60}")
        print(f"# Processing: {lecture_id}")
        print(f"{'#' * 60}")

        for stage in range(args.from_stage, args.to_stage + 1):
            success = run_stage(stage, lecture_id, extra_args)
            if not success:
                print(f"Error: stage {stage} failed for {lecture_id}", file=sys.stderr)
                failed_lectures.append((lecture_id, stage))
                break

    # Run assembly if requested
    if args.assemble and not failed_lectures:
        print(f"\n{'#' * 60}")
        print("# Running assembly (stage 7)")
        print(f"{'#' * 60}")
        success = run_stage(7, extra_args=extra_args)
        if not success:
            print("Error: assembly (stage 7) failed", file=sys.stderr)
            sys.exit(1)

    # Summary
    if failed_lectures:
        print(f"\n{'!' * 60}")
        print(f"FAILED: {len(failed_lectures)} lecture(s)")
        for lecture_id, stage in failed_lectures:
            print(f"  {lecture_id}: stage {stage}")
        print(f"{'!' * 60}")
        sys.exit(1)
    else:
        print(f"\n{'=' * 60}")
        print("SUCCESS: All stages completed")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
