#!/usr/bin/env python3
"""
Stage [11]: Generate Practice Exams (LLM)
============================================

Generates one or more practice exam papers (+ separate answer keys) from
the course's own material, in the format of any past exams uploaded to
input/exams/ (optional -- format analysis falls back to inferring a
sensible format from the course material alone if none were uploaded).

The actual generation logic lives in notely.pipeline.exams -- this script
is just the CLI wrapper around it, same pattern as
scripts/06_generate_notes.py / scripts/09_lecture_essentials.py.

Input:
    input/exams/*.pdf (optional -- past exams, used as a FORMAT template
        only; their actual questions are never reproduced)
    output/essentials.md (preferred) or output/study_guide.md (required)

Output:
    output/exams/exam_NN.md            the student-facing paper
    output/exams/exam_NN_key.md        the answer key
    output/exams/exam_NN_raw.json      raw request/response pairing
    output/exams/_format.json          cached format blueprint
    output/exams/_format_raw.json      raw request/response pairing

Usage:
    python scripts/11_generate_exam.py
    python scripts/11_generate_exam.py --count 3
    python scripts/11_generate_exam.py --questions 20
    python scripts/11_generate_exam.py --force

Config:
    ANTHROPIC_API_KEY  API key, loaded from .env if python-dotenv is available.
    EXAM_MODEL         Claude model id for exam generation
                       (default: "claude-sonnet-5"). Set in .env.

Notes:
    - Format analysis (one call) is cached to output/exams/_format.json and
      reused across papers and reruns unless --force.
    - Each paper is one API call producing both the paper and its answer
      key in a single response, split on a literal marker -- see
      notely.pipeline.exams.ANSWER_KEY_MARKER.
    - This stage is standalone, like stage 8 (PDF export) and stages 9/10
      (essentials) -- it is not part of the orchestrated 0-7 pipeline sweep.
"""

import argparse
import os
import sys
from pathlib import Path

# notely/ (ports, adapters) lives alongside scripts/ and webui/ at the
# project root, not on sys.path by default when this file is run directly
# (python scripts/11_generate_exam.py) -- same fix tests/conftest.py
# applies for test discovery. Must happen before the `from notely...`
# import below.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.pipeline.exams import (  # noqa: E402
    ANSWER_KEY_MARKER,
    COURSE_ESSENTIALS_PATH,
    DEFAULT_MODEL,
    EXAM_SYSTEM_PROMPT,
    FORMAT_SYSTEM_PROMPT,
    INPUT_EXAMS_DIR,
    OUTPUT_EXAMS_DIR,
    STUDY_GUIDE_PATH,
    build_exam_user_prompt,
    collect_uploaded_exam_pages,
    extract_exam_pages,
    generate_exam_format,
    generate_one_exam,
    load_dotenv_if_available,
    load_format_blueprint,
    process_exam_generation,
    split_exam_and_key,
)

__all__ = [
    "ANSWER_KEY_MARKER",
    "COURSE_ESSENTIALS_PATH",
    "DEFAULT_MODEL",
    "EXAM_SYSTEM_PROMPT",
    "FORMAT_SYSTEM_PROMPT",
    "INPUT_EXAMS_DIR",
    "OUTPUT_EXAMS_DIR",
    "STUDY_GUIDE_PATH",
    "build_exam_user_prompt",
    "collect_uploaded_exam_pages",
    "extract_exam_pages",
    "generate_exam_format",
    "generate_one_exam",
    "load_dotenv_if_available",
    "load_format_blueprint",
    "process_exam_generation",
    "split_exam_and_key",
    "main",
]


def main():
    parser = argparse.ArgumentParser(
        description="Stage [11]: Generate practice exam papers (+ answer keys) via the Claude API."
    )
    parser.add_argument("--count", type=int, default=1, help="number of exam papers to generate (default: 1)")
    parser.add_argument(
        "--questions", type=int, default=None, help="override the format blueprint's total question count"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="regenerate the format blueprint and any exam_NN.md that already exists",
    )
    args = parser.parse_args()

    if args.count < 1:
        parser.error("--count must be at least 1")

    load_dotenv_if_available()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set (check .env)", file=sys.stderr)
        sys.exit(1)

    # lazy import (via the adapter) — keeps py_compile / --help working
    # without the anthropic package installed
    from notely.adapters.anthropic_llm import AnthropicLlmClient

    llm_client = AnthropicLlmClient(max_retries=5)

    success = process_exam_generation(
        llm_client, count=args.count, force=args.force, questions_override=args.questions
    )
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
