#!/usr/bin/env python3
"""
Stage [10]: Distill Course Essentials (LLM)
==============================================

Calls the Claude API once with the whole assembled study guide
(output/study_guide.md) plus every stage-9 per-lecture sheet found (if
any), and asks it to distill a single course-level "must-know" summary.

The actual distillation logic lives in notely.pipeline.essentials -- this
script is just the CLI wrapper around it, same pattern as
scripts/07_assemble.py.

Input:
    output/study_guide.md (required -- run stage 7 first)
    output/essentials/<lecture_id>.md (optional -- stage 9's sheets, used
        as a shortlist of candidates if present)

Output:
    output/essentials.md
    output/essentials_raw.json
        Raw request/response pairing, for debugging.

Usage:
    python scripts/10_course_essentials.py
    python scripts/10_course_essentials.py --force

Config:
    ANTHROPIC_API_KEY  API key, loaded from .env if python-dotenv is available.
    ESSENTIALS_MODEL   Claude model id for essentials distillation
                       (default: "claude-haiku-4-5"). Set in .env.

Notes:
    - One API call total. If it fails, the stage fails (unlike stage 7's
      optional --topic-index pass, essentials.md is this stage's only
      deliverable, so there's nothing to fall back to).
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
# (python scripts/10_course_essentials.py) -- same fix tests/conftest.py
# applies for test discovery. Must happen before the `from notely...`
# import below.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.pipeline.essentials import (  # noqa: E402
    COURSE_ESSENTIALS_PATH,
    COURSE_SYSTEM_PROMPT,
    DEFAULT_MODEL,
    STUDY_GUIDE_PATH,
    build_course_user_prompt,
    generate_course_essentials,
    load_dotenv_if_available,
    process_course_essentials,
)

__all__ = [
    "COURSE_ESSENTIALS_PATH",
    "COURSE_SYSTEM_PROMPT",
    "DEFAULT_MODEL",
    "STUDY_GUIDE_PATH",
    "build_course_user_prompt",
    "generate_course_essentials",
    "load_dotenv_if_available",
    "process_course_essentials",
    "main",
]


def main():
    parser = argparse.ArgumentParser(
        description="Stage [10]: Distill a course-level must-know summary via the Claude API."
    )
    parser.add_argument(
        "--force", action="store_true", help="Regenerate even if output/essentials.md already exists"
    )
    args = parser.parse_args()

    load_dotenv_if_available()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set (check .env)", file=sys.stderr)
        sys.exit(1)

    # lazy import (via the adapter) — keeps py_compile / --help working
    # without the anthropic package installed
    from notely.adapters.anthropic_llm import AnthropicLlmClient

    llm_client = AnthropicLlmClient(max_retries=5)

    success = process_course_essentials(llm_client, force=args.force)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
