#!/usr/bin/env python3
"""Stage [8]: Export the assembled study guide (or one lecture's notes) to PDF.

Renders markdown -> HTML (protecting LaTeX math), then prints it with
headless Chrome/Chromium so MathJax typesets every formula exactly as a
browser would. Images are embedded from their existing on-disk paths.

Usage:
    python scripts/08_export_pdf.py                  # output/study_guide.pdf
    python scripts/08_export_pdf.py lecture03        # output/notes/lecture03.pdf
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"

CHROME_CANDIDATES = [
    "chromium",
    "chromium-browser",
    "google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script>
MathJax = {{ tex: {{ inlineMath: [['$', '$']], displayMath: [['$$', '$$']] }} }};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
<style>
  @page {{ size: A4; margin: 18mm 16mm; }}
  body {{ font: 11pt/1.5 Georgia, 'Times New Roman', serif; color: #1a1a1a; }}
  h1 {{ page-break-before: always; font-size: 1.6em; border-bottom: 2px solid #444; padding-bottom: .2em; }}
  body > h1:first-of-type {{ page-break-before: avoid; }}
  h2 {{ font-size: 1.2em; margin-top: 1.4em; }}
  img {{ max-width: 100%; max-height: 9cm; display: block; margin: .5em auto;
        border: 1px solid #ccc; page-break-inside: avoid; }}
  li {{ margin: .15em 0; }}
  hr {{ border: none; border-top: 1px solid #bbb; margin: 1.5em 0; }}
  mjx-container[display="true"] {{ page-break-inside: avoid; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def find_chrome() -> str:
    for cand in CHROME_CANDIDATES:
        path = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if path:
            return path
    raise RuntimeError(
        "no Chrome/Chromium found — install Google Chrome (mac) or chromium (linux)"
    )


def markdown_to_html(md_text: str, base_dir: Path) -> str:
    """Markdown -> HTML with $...$/$$...$$ passed through untouched for MathJax."""
    import markdown

    # Protect math from the markdown parser (underscores etc. would be mangled).
    stash = []

    def protect(match):
        stash.append(match.group(0))
        return f"\x00MATH{len(stash) - 1}\x00"

    guarded = re.sub(r"\$\$.*?\$\$|\$[^$\n]+\$", protect, md_text, flags=re.DOTALL)
    html = markdown.markdown(guarded, extensions=["tables", "sane_lists"])
    html = re.sub(r"\x00MATH(\d+)\x00", lambda m: stash[int(m.group(1))], html)
    # Relative image paths -> absolute file paths so Chrome finds them.
    html = html.replace('src="../', f'src="{base_dir.parent}/')
    html = re.sub(r'src="(?!/|file:|https?:)', f'src="{base_dir}/', html)
    return html


def export_pdf(md_path: Path, pdf_path: Path) -> None:
    html = HTML_TEMPLATE.format(body=markdown_to_html(md_path.read_text(), md_path.parent))
    chrome = find_chrome()

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, dir=str(md_path.parent)) as f:
        f.write(html)
        tmp_html = Path(f.name)
    try:
        print(f"[pdf] rendering {md_path.name} with {Path(chrome).name} (MathJax typesetting)...")
        # --virtual-time-budget lets MathJax finish typesetting before print.
        result = subprocess.run(
            [
                chrome, "--headless", "--disable-gpu", "--no-sandbox",
                "--virtual-time-budget=30000",
                f"--print-to-pdf={pdf_path}", "--no-pdf-header-footer",
                tmp_html.as_uri(),
            ],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0 or not pdf_path.exists():
            raise RuntimeError(f"chrome print failed:\n{result.stderr[-1500:]}")
    finally:
        tmp_html.unlink(missing_ok=True)
    print(f"[done] wrote {pdf_path} ({pdf_path.stat().st_size // 1024} KB)")


def main():
    parser = argparse.ArgumentParser(description="Export study guide or lecture notes to PDF.")
    parser.add_argument("lecture_id", nargs="?", help="export one lecture's notes instead of the full guide")
    parser.add_argument(
        "--output", help="write the PDF here instead of the default path "
        "(callers doing their own temp-file + atomic-rename dance, e.g. the web UI, pass this)"
    )
    args = parser.parse_args()

    if args.lecture_id:
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
