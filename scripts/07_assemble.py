#!/usr/bin/env python3
"""
Stage [7] Assembly

Concatenates per-lecture note files into a single study guide with a table
of contents, in sorted lecture order. The actual assembly logic lives in
notely.pipeline.assemble (Phase 5) -- this script is just the CLI wrapper
around it.

Input:
  - output/notes/<lecture_id>.md (multiple files)

Output:
  - output/study_guide.md
  - output/topic_index_raw.json (only with --topic-index: the raw prompt/
    response pairing, for debugging a bad index the same way stage 6 keeps
    per-slide raw pairings)

Optional cross-lecture LLM pass: --topic-index (off by default -- costs a
real API call over the whole assembled guide; see generate_topic_index).
"""

import argparse
import sys
from pathlib import Path

# notely/ (ports, adapters) lives alongside scripts/ and webui/ at the
# project root, not on sys.path by default when this file is run directly
# (python scripts/07_assemble.py) -- same fix tests/conftest.py applies for
# test discovery. Must happen before the `from notely...` import below.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.pipeline.assemble import (  # noqa: E402
    TOPIC_INDEX_SYSTEM_PROMPT,
    assemble_guide,
    build_table_of_contents,
    find_lecture_notes,
    generate_topic_index,
    get_project_root,
    load_dotenv_if_available,
    parse_lecture_id_from_filename,
    read_lecture_notes,
)

__all__ = [
    "TOPIC_INDEX_SYSTEM_PROMPT",
    "assemble_guide",
    "build_table_of_contents",
    "find_lecture_notes",
    "generate_topic_index",
    "get_project_root",
    "load_dotenv_if_available",
    "parse_lecture_id_from_filename",
    "read_lecture_notes",
    "main",
]


def main():
    parser = argparse.ArgumentParser(description="Stage [7] Assembly - Build final study guide")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output")
    parser.add_argument(
        "--topic-index",
        action="store_true",
        help="also generate a cross-lecture topic index via one extra Claude API call "
        "over the whole assembled guide (off by default -- real cost, potentially "
        "hundreds of thousands of tokens for a full course; needs ANTHROPIC_API_KEY)",
    )

    args = parser.parse_args()

    success = assemble_guide(force=args.force, topic_index=args.topic_index)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
