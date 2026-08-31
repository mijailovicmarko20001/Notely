#!/usr/bin/env python3
"""
Stage [2]: Slide text extraction.

Reads a slide deck (pptx or pdf) from input/slides/<lecture_id>.{pptx,pdf},
extracts per-slide text (title, body, speaker notes), renders each slide to
a PNG, and writes output/slides_extracted/<lecture_id>.json.

The actual extraction logic lives in notely.pipeline.slides (Phase 5) --
this script is just the CLI wrapper around it.

Usage:
    python scripts/02_extract_slides.py lecture01
    python scripts/02_extract_slides.py --all
    python scripts/02_extract_slides.py lecture01 --force
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
# directly (python scripts/02_extract_slides.py) -- same fix
# tests/conftest.py applies for test discovery. Must happen before the
# `from notely...` import below.
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.cli import require_lecture_id_or_all  # noqa: E402
from notely.pipeline.slides import (  # noqa: E402
    OUTPUT_DIR,
    PROJECT_ROOT,
    extract_from_pdf,
    extract_from_pptx,
    process_lecture,
    render_pdf_to_images,
)
from notely.paths import SLIDES_DIR  # noqa: E402

__all__ = [
    "OUTPUT_DIR",
    "PROJECT_ROOT",
    "SLIDES_DIR",
    "extract_from_pdf",
    "extract_from_pptx",
    "process_lecture",
    "render_pdf_to_images",
    "main",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage [2]: extract slide text + render images.")
    parser.add_argument("lecture_id", nargs="?", help="e.g. lecture01 (input/slides/lecture01.pptx|.pdf)")
    parser.add_argument("--all", action="store_true", help="process every deck found in input/slides/")
    parser.add_argument("--force", action="store_true", help="re-run even if output already exists")
    args = parser.parse_args()

    require_lecture_id_or_all(parser, args)

    if args.all:
        lecture_ids = sorted({p.stem for p in SLIDES_DIR.glob("*") if p.suffix.lower() in (".pptx", ".pdf")})
        if not lecture_ids:
            print(f"No slide decks found in {SLIDES_DIR}")
            return
    else:
        lecture_ids = [args.lecture_id]

    failures = [lid for lid in lecture_ids if not process_lecture(lid, force=args.force)]
    if failures:
        print(f"FAILED: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
