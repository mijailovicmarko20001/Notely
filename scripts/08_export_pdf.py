#!/usr/bin/env python3
"""Stage [8]: Export the assembled study guide (or one lecture's notes) to PDF.

Renders markdown -> HTML (protecting LaTeX math), then prints it with
headless Chrome/Chromium so MathJax typesets every formula exactly as a
browser would. Images are embedded from their existing on-disk paths.

The actual rendering logic lives in notely.pipeline.export (Phase 5) --
this script is just the CLI wrapper around it.

Usage:
    python scripts/08_export_pdf.py                  # output/study_guide.pdf
    python scripts/08_export_pdf.py lecture03        # output/notes/lecture03.pdf
    python scripts/08_export_pdf.py --essentials      # output/essentials.pdf
    python scripts/08_export_pdf.py lecture03 --essentials  # output/essentials/lecture03.pdf
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
# directly (python scripts/08_export_pdf.py) -- same fix
# tests/conftest.py applies for test discovery. Must happen before the
# `from notely...` import below.
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.paths import OUTPUT_DIR  # noqa: E402
from notely.pipeline.export import HTML_TEMPLATE, export_pdf, markdown_to_html  # noqa: E402

__all__ = ["HTML_TEMPLATE", "export_pdf", "markdown_to_html", "main"]


def main():
    parser = argparse.ArgumentParser(description="Export study guide or lecture notes to PDF.")
    parser.add_argument("lecture_id", nargs="?", help="export one lecture's notes instead of the full guide")
    parser.add_argument(
        "--essentials",
        action="store_true",
        help="export the distilled essentials (notely.pipeline.essentials, stages 9/10) instead of "
        "the full guide/notes -- output/essentials.md, or output/essentials/<lecture_id>.md "
        "if a lecture_id is given",
    )
    parser.add_argument(
        "--output",
        help="write the PDF here instead of the default path "
        "(callers doing their own temp-file + atomic-rename dance, e.g. the web UI, pass this)",
    )
    args = parser.parse_args()

    if args.essentials:
        if args.lecture_id:
            md_path = OUTPUT_DIR / "essentials" / f"{args.lecture_id}.md"
            pdf_path = OUTPUT_DIR / "essentials" / f"{args.lecture_id}.pdf"
        else:
            md_path = OUTPUT_DIR / "essentials.md"
            pdf_path = OUTPUT_DIR / "essentials.pdf"
    elif args.lecture_id:
        md_path = OUTPUT_DIR / "notes" / f"{args.lecture_id}.md"
        pdf_path = OUTPUT_DIR / "notes" / f"{args.lecture_id}.pdf"
    else:
        md_path = OUTPUT_DIR / "study_guide.md"
        pdf_path = OUTPUT_DIR / "study_guide.pdf"
    if args.output:
        pdf_path = Path(args.output)

    if not md_path.exists():
        print(f"ERROR: {md_path} not found — run the pipeline first", file=sys.stderr)
        sys.exit(1)
    export_pdf(md_path, pdf_path)


if __name__ == "__main__":
    main()
