"""Stage [8]: export the assembled study guide (or one lecture's notes) to
PDF -- moved here from scripts/08_export_pdf.py (Phase 5), which is now a
thin CLI wrapper around export_pdf().

Renders markdown -> HTML (protecting LaTeX math), then prints it with
headless Chrome/Chromium so MathJax typesets every formula exactly as a
browser would. Images are embedded from their existing on-disk paths.
"""

import re
import tempfile
from pathlib import Path

from ..adapters.chrome_html_to_pdf import ChromeHtmlToPdf

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


def export_pdf(md_path: Path, pdf_path: Path, html_to_pdf=None) -> None:
    """html_to_pdf: an HtmlToPdf (see notely.ports), defaults to the real
    Chrome-backed adapter; tests inject a fake instead of needing
    Chrome/Chromium installed."""
    if html_to_pdf is None:
        html_to_pdf = ChromeHtmlToPdf()
    html = HTML_TEMPLATE.format(body=markdown_to_html(md_path.read_text(encoding="utf-8"), md_path.parent))

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, dir=str(md_path.parent)) as f:
        f.write(html)
        tmp_html = Path(f.name)
    try:
        print(f"[pdf] rendering {md_path.name} (headless Chrome, MathJax typesetting)...")
        html_to_pdf.render(tmp_html.as_uri(), pdf_path)
    finally:
        tmp_html.unlink(missing_ok=True)
    print(f"[done] wrote {pdf_path} ({pdf_path.stat().st_size // 1024} KB)")
