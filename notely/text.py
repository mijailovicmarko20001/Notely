"""Shared text-normalization helpers.

fold_diacritics was duplicated near-identically in stage 4 (matching OCR
text against the deck's own extracted text) and stage 5 (matching spoken
EXAMPLE_CUES against transcript text) -- stage 4's docstring explicitly
noted the duplication and its rationale ("each stage script is meant to
run standalone"), which this package makes moot: both stages already
depend on notely/ for paths and I/O.
"""

import unicodedata


def fold_diacritics(text: str) -> str:
    """NFKD-decompose and drop combining marks, so an accented and
    unaccented spelling of the same word compare equal (Serbian-latin
    text/OCR/ASR output is inconsistent about diacritics -- vežbanje /
    vezbanje, č/ć/š/ž/đ dropped or misread). Does not lowercase --
    callers that want case-insensitive comparison too do that themselves."""
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in normalized if not unicodedata.combining(c))
