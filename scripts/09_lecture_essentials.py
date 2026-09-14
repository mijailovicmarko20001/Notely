#!/usr/bin/env python3
"""
Stage [9]: Distill Lecture Essentials (LLM)
=============================================

For each lecture, calls the Claude API with that lecture's finished notes
(output/notes/<lecture_id>.md) and asks it to distill a short "must-know"
sheet: verbatim definitions/formulas and exam flags kept, restated slide
bullets dropped.

The actual distillation logic lives in notely.pipeline.essentials -- this
script is just the CLI wrapper around it, same pattern as
scripts/06_generate_notes.py.

Input:
    output/notes/<lecture_id>.md

Output:
    output/essentials/<lecture_id>.md
    output/essentials/<lecture_id>_raw.json
        Raw request/response pairing, for debugging:
        {"prompt": ..., "response": ..., "model": ..., "usage": ...}

Usage:
    python scripts/09_lecture_essentials.py <lecture_id>
    python scripts/09_lecture_essentials.py --all
    python scripts/09_lecture_essentials.py <lecture_id> --force

Config:
    ANTHROPIC_API_KEY  API key, loaded from .env if python-dotenv is available.
    ESSENTIALS_MODEL   Claude model id for essentials distillation
                       (default: "claude-haiku-4-5"). Set in .env.

Notes:
    - One API call per lecture (not per slide) -- a distillation pass over
      already-generated notes, not a synthesis from scratch.
    - A failed lecture (after the SDK's built-in retries are exhausted)
      does not abort a --all run: it's listed at the end, and processing
      continues with the next lecture.
    - This stage is standalone, like stage 8 (PDF export) -- it is not
      part of the orchestrated 0-7 pipeline sweep. Run it explicitly, or
      via run_pipeline.py's --essentials flag.
"""

import argparse
import os
import sys
from pathlib import Path

# notely/ (ports, adapters) lives alongside scripts/ and webui/ at the
# project root, not on sys.path by default when this file is run directly
# (python scripts/09_lecture_essentials.py) -- same fix tests/conftest.py
# applies for test discovery. Must happen before the `from notely...`
# import below.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.cli import require_lecture_id_or_all  # noqa: E402
from notely.pipeline.essentials import (  # noqa: E402
    DEFAULT_MODEL,
    INPUT_NOTES_DIR,
    LECTURE_SYSTEM_PROMPT,
    OUTPUT_ESSENTIALS_DIR,
    build_lecture_user_prompt,
    load_dotenv_if_available,
    process_lecture_essentials,
)

__all__ = [
    "DEFAULT_MODEL",
    "INPUT_NOTES_DIR",
    "LECTURE_SYSTEM_PROMPT",
    "OUTPUT_ESSENTIALS_DIR",
    "build_lecture_user_prompt",
    "load_dotenv_if_available",
    "process_lecture_essentials",
    "main",
]


def main():
    parser = argparse.ArgumentParser(
        description="Stage [9]: Distill a per-lecture must-know sheet via the Claude API."
    )
    parser.add_argument(
        "lecture_id",
        nargs="?",
        default=None,
        help="Lecture id, e.g. lecture01 (matches output/notes/<lecture_id>.md)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Distill essentials for every lecture note found in output/notes/",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if output/essentials/<lecture_id>.md already exists",
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
        note_files = sorted(INPUT_NOTES_DIR.glob("*.md"))
        if not note_files:
            print(f"No lecture notes found in {INPUT_NOTES_DIR}")
            return
        lecture_ids = [p.stem for p in note_files]
    else:
        lecture_ids = [args.lecture_id]

    total = len(lecture_ids)
    failed = []
    for i, lecture_id in enumerate(lecture_ids, start=1):
        ok = process_lecture_essentials(llm_client, lecture_id, i, total, force=args.force)
        if not ok:
            failed.append(lecture_id)

    if failed:
        print(f"FAILED: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
