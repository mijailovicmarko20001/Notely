"""Tests for webui/decks.py's PDF paths -- merge_pool (pypdf), pool upload
replacement -- and webui/lecture_match.py's _extract_preview_text pdf
branch (pypdfium2). The PyMuPDF -> pypdfium2/pypdf swap (OPEN_SOURCE_TODO.md
O2) had zero test coverage before this."""

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pdf_fixtures import make_pdf_bytes  # noqa: E402

from webui import config, decks, lecture_match  # noqa: E402
from webui.errors import ValidationError  # noqa: E402


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


def test_merge_pool_ignores_a_previously_merged_deck(tmp_path):
    pool_dir = tmp_path / "_pool"
    pool_dir.mkdir()
    (pool_dir / "deck.pdf").write_bytes(make_pdf_bytes(["Real content"]))
    # merge_pool writes _merged.pdf back into the pool dir it reads from. Its
    # image-only pages are deliberately never deduped, so folding a previous
    # merge back in would grow the deck by one copy of them per run.
    (pool_dir / decks.MERGED_DECK_NAME).write_bytes(make_pdf_bytes(["", ""]))

    result = decks.merge_pool(pool_dir)

    assert result["scanned_pages"] == 1
    assert result["total_pages"] == 1
    assert [p.name for p in result["pool_files"]] == ["deck.pdf"]


# --- pool uploads replace the pool rather than accumulating into it --------


def _upload(name: str, pages: list[str]):
    from starlette.datastructures import UploadFile

    return UploadFile(filename=name, file=io.BytesIO(make_pdf_bytes(pages)))


@pytest.fixture()
def slides_dir(tmp_path, monkeypatch):
    """decks.py reaches paths through `config.SLIDES_DIR`, so monkeypatching
    the config attribute is enough (see tests/webui/conftest.py's docstring)."""
    d = tmp_path / "slides"
    d.mkdir()
    monkeypatch.setattr(config, "SLIDES_DIR", d)
    return d


@pytest.mark.anyio
async def test_save_pool_uploads_replaces_a_previous_courses_decks(slides_dir):
    pool_dir = slides_dir / "_pool"
    pool_dir.mkdir()
    (pool_dir / "old_course_deck.pdf").write_bytes(make_pdf_bytes(["Last term"]))
    (pool_dir / decks.MERGED_DECK_NAME).write_bytes(make_pdf_bytes(["Last term"]))

    saved = await decks.save_pool_uploads([_upload("new_course_deck.pdf", ["This term"])])

    assert [p.name for p in saved] == ["new_course_deck.pdf"]
    # The old course's decks are gone, so they can't leak into the merge and
    # end up in every lecture's combined deck.
    assert sorted(p.name for p in pool_dir.iterdir()) == ["new_course_deck.pdf"]


@pytest.mark.anyio
async def test_save_pool_uploads_leaves_pool_intact_when_an_upload_is_rejected(slides_dir):
    pool_dir = slides_dir / "_pool"
    pool_dir.mkdir()
    (pool_dir / "existing.pdf").write_bytes(make_pdf_bytes(["Keep me"]))

    with pytest.raises(ValidationError):
        await decks.save_pool_uploads(
            [
                _upload("good.pdf", ["Fine"]),
                _upload("deck.pptx", ["Not a PDF"]),
            ]
        )

    # Replacement is all-or-nothing: a rejected file in the batch must not
    # leave the user with a half-written pool (or none at all).
    assert sorted(p.name for p in pool_dir.iterdir()) == ["existing.pdf"]
    assert sorted(p.name for p in slides_dir.iterdir()) == ["_pool"]  # no staging left over


def test_extract_preview_text_reads_first_two_pdf_pages(tmp_path):
    path = tmp_path / "deck.pdf"
    path.write_bytes(make_pdf_bytes(["Title Slide", "Second Slide", "Third Slide"]))

    text = lecture_match._extract_preview_text(path, "pdf")

    assert "Title Slide" in text
    assert "Second Slide" in text
    assert "Third Slide" not in text  # only the first two pages are read


def test_extract_preview_text_returns_empty_on_unreadable_pdf(tmp_path):
    path = tmp_path / "deck.pdf"
    path.write_bytes(b"not a real pdf")

    text = lecture_match._extract_preview_text(path, "pdf")

    assert text == ""
