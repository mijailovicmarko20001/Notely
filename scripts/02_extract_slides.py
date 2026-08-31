#!/usr/bin/env python3
"""
Stage [2]: Slide text extraction.

Reads a slide deck (pptx or pdf) from input/slides/<lecture_id>.{pptx,pdf},
extracts per-slide text (title, body, speaker notes), renders each slide to
a PNG, and writes output/slides_extracted/<lecture_id>.json.

Usage:
    python scripts/02_extract_slides.py lecture01
    python scripts/02_extract_slides.py --all
    python scripts/02_extract_slides.py lecture01 --force
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SLIDES_DIR = PROJECT_ROOT / "input" / "slides"
OUTPUT_DIR = PROJECT_ROOT / "output" / "slides_extracted"


def find_soffice() -> str | None:
    """Locate the LibreOffice headless binary, including common macOS install paths."""
    candidates = [
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def convert_pptx_to_pdf(pptx_path: Path, out_dir: Path) -> Path:
    """Convert a .pptx to .pdf via headless LibreOffice, for rendering purposes only."""
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError(
            "soffice (LibreOffice) not found on PATH — required to render .pptx "
            "slides to images.\nInstall it with: brew install --cask libreoffice"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx_path)],
        check=True,
        capture_output=True,
    )
    pdf_path = out_dir / f"{pptx_path.stem}.pdf"
    if not pdf_path.exists():
        raise RuntimeError(f"soffice conversion did not produce expected file: {pdf_path}")
    return pdf_path


def render_pdf_to_images(pdf_path: Path, image_dir: Path, dpi: int = 150) -> list[str]:
    """Render each page of a PDF to a PNG, one per slide. Returns project-root-relative paths."""
    import pypdfium2 as pdfium

    image_dir.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72  # pypdfium2: render(scale=1) == 72 DPI (1 px per PDF point)
    image_paths = []
    with pdfium.PdfDocument(str(pdf_path)) as doc:
        for i, page in enumerate(doc, start=1):
            try:
                bitmap = page.render(scale=scale)
                try:
                    image_path = image_dir / f"slide_{i:03d}.png"
                    bitmap.to_pil().save(str(image_path))
                    image_paths.append(str(image_path.relative_to(PROJECT_ROOT)))
                finally:
                    bitmap.close()
            finally:
                page.close()
    return image_paths


def extract_from_pptx(pptx_path: Path, image_dir: Path) -> list[dict]:
    """Extract text via python-pptx; render images by converting to PDF first."""
    from pptx import Presentation

    prs = Presentation(str(pptx_path))
    text_by_slide = []
    for slide in prs.slides:
        title = ""
        title_shape = slide.shapes.title
        body_parts = []
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            text = shape.text_frame.text.strip()
            if not text:
                continue
            if title_shape is not None and shape.shape_id == title_shape.shape_id:
                title = text
            else:
                body_parts.append(text)
        if not title and body_parts:
            title = body_parts[0].splitlines()[0].strip()

        notes_text = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes_text = slide.notes_slide.notes_text_frame.text.strip()

        text_by_slide.append(
            {"title": title, "body_text": "\n".join(body_parts).strip(), "notes_text": notes_text}
        )

    pdf_path = convert_pptx_to_pdf(pptx_path, image_dir)
    image_paths = render_pdf_to_images(pdf_path, image_dir)

    if len(image_paths) != len(text_by_slide):
        print(
            f"  warning: pptx slide count ({len(text_by_slide)}) != rendered page "
            f"count ({len(image_paths)}) for {pptx_path.name}"
        )

    slides = []
    for i, info in enumerate(text_by_slide, start=1):
        image_path = image_paths[i - 1] if i - 1 < len(image_paths) else ""
        slides.append({"slide_number": i, "image_path": image_path, **info})
    return slides


def extract_from_pdf(pdf_path: Path, image_dir: Path) -> list[dict]:
    """Extract text and render images directly from a PDF via pypdfium2."""
    import pypdfium2 as pdfium

    image_paths = render_pdf_to_images(pdf_path, image_dir)

    slides = []
    with pdfium.PdfDocument(str(pdf_path)) as doc:
        for i, page in enumerate(doc, start=1):
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                finally:
                    textpage.close()
            finally:
                page.close()
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            title = lines[0] if lines else ""
            body_text = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
            image_path = image_paths[i - 1] if i - 1 < len(image_paths) else ""
            slides.append(
                {
                    "slide_number": i,
                    "title": title,
                    "body_text": body_text,
                    "notes_text": "",
                    "image_path": image_path,
                }
            )
    return slides


def process_lecture(lecture_id: str, force: bool) -> bool:
    """Returns False only when no slide deck was found (the caller should
    treat that as a failure); an already-done skip and a real successful
    run both return True."""
    out_json = OUTPUT_DIR / f"{lecture_id}.json"
    if out_json.exists() and not force:
        print(f"[{lecture_id}] {out_json} already exists, skipping (use --force to re-run)")
        return True

    pptx_path = SLIDES_DIR / f"{lecture_id}.pptx"
    pdf_path = SLIDES_DIR / f"{lecture_id}.pdf"
    image_dir = OUTPUT_DIR / f"{lecture_id}_images"

    if pptx_path.exists():
        slides = extract_from_pptx(pptx_path, image_dir)
    elif pdf_path.exists():
        slides = extract_from_pdf(pdf_path, image_dir)
    else:
        print(f"[{lecture_id}] no deck found (looked for {pptx_path.name} / {pdf_path.name}) in {SLIDES_DIR}")
        return False

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Temp file + atomic rename: a killed process can never leave a
    # truncated-but-non-empty artifact that a later run's exists()-and-
    # nonempty skip check would wrongly trust as done.
    tmp_json = out_json.with_name(f"{out_json.name}.tmp{os.getpid()}")
    tmp_json.write_text(json.dumps(slides, indent=2))
    tmp_json.replace(out_json)
    print(f"[{lecture_id}] wrote {len(slides)} slides -> {out_json}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage [2]: extract slide text + render images.")
    parser.add_argument("lecture_id", nargs="?", help="e.g. lecture01 (input/slides/lecture01.pptx|.pdf)")
    parser.add_argument("--all", action="store_true", help="process every deck found in input/slides/")
    parser.add_argument("--force", action="store_true", help="re-run even if output already exists")
    args = parser.parse_args()

    if not args.all and not args.lecture_id:
        parser.error("provide a lecture_id, or use --all")

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
