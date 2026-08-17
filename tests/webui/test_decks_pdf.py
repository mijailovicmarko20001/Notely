"""Tests for webui/decks.py's PDF paths -- merge_pool (pypdf) and
_extract_preview_text's pdf branch (pypdfium2). The PyMuPDF -> pypdfium2/
pypdf swap (OPEN_SOURCE_TODO.md O2) had zero test coverage before this."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pdf_fixtures import make_pdf_bytes  # noqa: E402

from webui import decks  # noqa: E402


def test_merge_pool_dedupes_identical_pages_across_files(tmp_path):
    pool_dir = tmp_path / "_pool"
    pool_dir.mkdir()
    # deck_a: 2 pages, deck_b: repeats deck_a's first page then one new page.
    (pool_dir / "deck_a.pdf").write_bytes(make_pdf_bytes(["Intro", "CORDIC basics"]))
    (pool_dir / "deck_b.pdf").write_bytes(make_pdf_bytes(["Intro", "New material"]))

    result = decks.merge_pool(pool_dir)

    assert result["scanned_pages"] == 4
    assert result["total_pages"] == 3  # the repeated "Intro" page dropped
    assert result["merged_path"].exists()

    from pypdf import PdfReader

    merged = PdfReader(result["merged_path"])
    assert len(merged.pages) == 3
    texts = [p.extract_text() for p in merged.pages]
    assert texts[0] == "Intro"
    assert texts[1] == "CORDIC basics"
    assert texts[2] == "New material"


def test_merge_pool_never_dedupes_blank_pages_against_each_other(tmp_path):
    pool_dir = tmp_path / "_pool"
    pool_dir.mkdir()
    (pool_dir / "deck.pdf").write_bytes(make_pdf_bytes(["", "", "Real content"]))

    result = decks.merge_pool(pool_dir)

    # Two blank (image-only-style) pages must both survive -- collapsing
    # them on an empty-string hash match would wrongly merge visually
    # distinct slides.
    assert result["total_pages"] == 3


def test_merge_pool_processes_files_in_filename_order(tmp_path):
    pool_dir = tmp_path / "_pool"
    pool_dir.mkdir()
    (pool_dir / "b_second.pdf").write_bytes(make_pdf_bytes(["Second file"]))
    (pool_dir / "a_first.pdf").write_bytes(make_pdf_bytes(["First file"]))

    result = decks.merge_pool(pool_dir)

    from pypdf import PdfReader

    merged = PdfReader(result["merged_path"])
    assert [p.extract_text() for p in merged.pages] == ["First file", "Second file"]


def test_extract_preview_text_reads_first_two_pdf_pages(tmp_path):
    path = tmp_path / "deck.pdf"
    path.write_bytes(make_pdf_bytes(["Title Slide", "Second Slide", "Third Slide"]))

    text = decks._extract_preview_text(path, "pdf")

    assert "Title Slide" in text
    assert "Second Slide" in text
    assert "Third Slide" not in text  # only the first two pages are read


def test_extract_preview_text_returns_empty_on_unreadable_pdf(tmp_path):
    path = tmp_path / "deck.pdf"
    path.write_bytes(b"not a real pdf")

    text = decks._extract_preview_text(path, "pdf")

    assert text == ""
