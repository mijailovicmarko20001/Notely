"""Ocr adapter backed by pytesseract.

Lazily imports pytesseract and PIL inside image_to_text (not at module top
level) -- same convention as every stage script's own lazy imports and as
notely.adapters.anthropic_llm: offline test collection and `python -m
py_compile` work without the dependency installed.
"""

import sys
from pathlib import Path


class TesseractOcr:
    """Real Ocr, via pytesseract. Never raises -- an OCR failure prints a
    warning and returns '', matching the pre-port ocr_frame's own
    reasoning: stage 4's per-frame OCR failures shouldn't kill the whole
    run."""

    def image_to_text(self, image_path: Path, lang: str) -> str:
        import pytesseract
        from PIL import Image

        try:
            with Image.open(image_path) as img:
                text = pytesseract.image_to_string(img, lang=lang)
        except Exception as exc:  # noqa: BLE001 - OCR failures shouldn't kill the whole run
            print(f"  warning: OCR failed for {image_path}: {exc}", file=sys.stderr)
            return ""
        return text.strip()
