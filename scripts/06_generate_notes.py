#!/usr/bin/env python3
"""
Stage [6]: Note Generation (LLM)
==================================

For each slide of a lecture, calls the Claude API with the slide's text,
speaker notes, and matched transcript chunk, and asks it to produce
structured per-slide study notes that preserve anything the professor said
that isn't on the slide.

The actual generation logic lives in notely.pipeline.notes, and
worked-example confirmation in notely.pipeline.examples (Phase 5) -- this
script is just the CLI wrapper around them.

Input:
    output/segmented_transcripts/<lecture_id>.json
        [{"slide_number": int, "slide_text": str, "notes_text": str,
          "transcript_text": str, "start": float, "end": float|None,
          "merged_from": [int]}, ...]

Output:
    output/notes/<lecture_id>.md
        Concatenated per-slide notes with a lecture header.
    output/notes/<lecture_id>_raw/slide_NNN.json
        Raw request/response pairing per slide, for debugging:
        {"prompt": ..., "response": ..., "model": ..., "usage": ...}
    output/notes/<lecture_id>_examples.json (only when NOTES_DETECT_EXAMPLES=1)
        Cached worked-example confirmation results:
        {"model": ..., "candidates_seen": int, "confirmed": [...], "rejected": [...],
         "usage": {"input_tokens": int, "output_tokens": int,
                    "cache_creation_input_tokens": int, "cache_read_input_tokens": int}}
    output/notes/<lecture_id>_raw/example_NNN.json (only when NOTES_DETECT_EXAMPLES=1)
        Raw request/response pairing per confirmed/rejected candidate.

Usage:
    python scripts/06_generate_notes.py <lecture_id>
    python scripts/06_generate_notes.py --all
    python scripts/06_generate_notes.py <lecture_id> --force

Config:
    ANTHROPIC_API_KEY     API key, loaded from .env if python-dotenv is available.
    NOTES_MODEL           Claude model id for note generation
                          (default: "claude-sonnet-5"). Set in .env.
    NOTES_DETECT_EXAMPLES Opt-in (off by default): confirm/caption stage 4/5's
                          worked-example candidates and embed them in the notes.
                          Set to "1" in .env.
    NOTES_EXAMPLES_MODEL  Claude model id for example confirmation
                          (default: "claude-haiku-4-5"). Set in .env.
    NOTES_EXAMPLES_MAX    Max example candidates confirmed per lecture
                          (default: 40). Set in .env.

Notes:
    - One API call per slide.
    - Slides with both empty slide_text and empty transcript_text are
      skipped (no API call) with a placeholder line in the output markdown.
    - A single failed slide (after the SDK's built-in retries are
      exhausted) does not abort the lecture: it gets an error placeholder
      in the markdown, is listed at the end of the file, and processing
      continues with the next slide.
    - `anthropic` is imported lazily (inside main) so that
      `python -m py_compile` and `--help` work even without the dependency
      installed.
"""

import argparse
import os
import sys
from pathlib import Path

# Only needed to bootstrap the `from notely...` import below (finding
# notely/ on sys.path) -- notely.paths.PROJECT_ROOT is the same value and
# is what the rest of this file uses.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent

# notely/ (ports, paths) lives alongside scripts/ and webui/ at the
# project root, not on sys.path by default when this file is run directly
# (python scripts/06_generate_notes.py) -- same fix tests/conftest.py
# applies for test discovery. Must happen before the `from notely...`
# import below.
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.cli import require_lecture_id_or_all  # noqa: E402
from notely.pipeline.notes import (  # noqa: E402
    DEFAULT_MODEL,
    INPUT_SEGMENTED_DIR,
    MAX_TOKENS,
    OUTPUT_NOTES_DIR,
    SYSTEM_PROMPT,
    add_slide_number_to_heading,
    build_user_prompt,
    generate_slide_note,
    load_dotenv_if_available,
    process_lecture,
)

# Worked-example confirmation lives in notely.pipeline.examples -- re-exported
# here too since this script used to define them all in one file.
from notely.pipeline.examples import (  # noqa: E402
    DEFAULT_EXAMPLES_MAX_PER_LECTURE,
    DEFAULT_EXAMPLES_MODEL,
    EXAMPLE_SYSTEM_PROMPT,
    build_example_prompt,
    build_examples_markdown,
    confirm_example,
    fmt_ts,
    frame_md_path,
    load_frame_image_b64,
    parse_example_verdict,
    rank_example_candidates,
    run_example_confirmation,
    sum_usage,
)

__all__ = [
    "DEFAULT_MODEL",
    "INPUT_SEGMENTED_DIR",
    "MAX_TOKENS",
    "OUTPUT_NOTES_DIR",
    "SYSTEM_PROMPT",
    "add_slide_number_to_heading",
    "build_user_prompt",
    "generate_slide_note",
    "load_dotenv_if_available",
    "process_lecture",
    "DEFAULT_EXAMPLES_MAX_PER_LECTURE",
    "DEFAULT_EXAMPLES_MODEL",
    "EXAMPLE_SYSTEM_PROMPT",
    "build_example_prompt",
    "build_examples_markdown",
    "confirm_example",
    "fmt_ts",
    "frame_md_path",
    "load_frame_image_b64",
    "parse_example_verdict",
    "rank_example_candidates",
    "run_example_confirmation",
    "sum_usage",
    "main",
]


def main():
    parser = argparse.ArgumentParser(
        description="Stage [6]: Generate per-slide study notes via the Claude API."
    )
    parser.add_argument(
        "lecture_id",
        nargs="?",
        default=None,
        help="Lecture id, e.g. lecture01 (matches output/segmented_transcripts/<lecture_id>.json)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate notes for every segmented transcript found in output/segmented_transcripts/",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if output/notes/<lecture_id>.md already exists",
    )
    args = parser.parse_args()

    require_lecture_id_or_all(parser, args)

    load_dotenv_if_available()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set (check .env)", file=sys.stderr)
        sys.exit(1)

    # lazy import (via the adapter) — keeps py_compile / --help working
    # without the anthropic package installed
    from notely.adapters.anthropic_llm import AnthropicLlmClient

    llm_client = AnthropicLlmClient(max_retries=5)

    if args.all:
        input_files = sorted(INPUT_SEGMENTED_DIR.glob("*.json"))
        if not input_files:
            print(f"No segmented transcripts found in {INPUT_SEGMENTED_DIR}")
            return
        lecture_ids = [p.stem for p in input_files]
    else:
        lecture_ids = [args.lecture_id]

    failed = []
    for lecture_id in lecture_ids:
        ok = process_lecture(llm_client, lecture_id, force=args.force)
        if not ok:
            failed.append(lecture_id)

    if failed:
        print(f"FAILED: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
