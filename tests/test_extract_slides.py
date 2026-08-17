"""Tests for scripts/02_extract_slides.py's PDF path (pypdfium2) -- the
PyMuPDF -> pypdfium2 swap (see OPEN_SOURCE_TODO.md O2) had zero test coverage
before this."""

from conftest import load_stage
from pdf_fixtures import make_pdf_bytes

extract_slides = load_stage("02_extract_slides.py")


def test_render_pdf_to_images_writes_one_png_per_page(tmp_path):
    pdf_path = tmp_path / "deck.pdf"
    pdf_path.write_bytes(make_pdf_bytes(["Page One", "Page Two", "Page Three"]))
    image_dir = tmp_path / "images"

    # PROJECT_ROOT-relative paths are meaningless from a tmp dir; monkeypatch
    # it to tmp_path so relative_to() doesn't raise.
    extract_slides.PROJECT_ROOT = tmp_path
    paths = extract_slides.render_pdf_to_images(pdf_path, image_dir, dpi=72)

    assert len(paths) == 3
    for i, rel_path in enumerate(paths, start=1):
        png = tmp_path / rel_path
        assert png.exists() and png.stat().st_size > 0
        assert png.name == f"slide_{i:03d}.png"


def test_render_pdf_to_images_size_scales_with_dpi(tmp_path):
    from PIL import Image

    pdf_path = tmp_path / "deck.pdf"
    pdf_path.write_bytes(make_pdf_bytes(["One page"]))
    image_dir = tmp_path / "images"
    extract_slides.PROJECT_ROOT = tmp_path

    paths_72 = extract_slides.render_pdf_to_images(pdf_path, image_dir, dpi=72)
    size_72 = Image.open(tmp_path / paths_72[0]).size

    image_dir_150 = tmp_path / "images150"
    paths_150 = extract_slides.render_pdf_to_images(pdf_path, image_dir_150, dpi=150)
    size_150 = Image.open(tmp_path / paths_150[0]).size

    # MediaBox is 200x100 pt; at 72 DPI that's 1px/pt, at 150 DPI ~150/72x.
    assert size_72 == (200, 100)
    assert size_150[0] > size_72[0] and size_150[1] > size_72[1]


def test_extract_from_pdf_splits_title_from_body(tmp_path):
    pdf_path = tmp_path / "deck.pdf"
    pdf_path.write_bytes(make_pdf_bytes(["My Title\nBody line one"]))
    image_dir = tmp_path / "images"
    extract_slides.PROJECT_ROOT = tmp_path

    slides = extract_slides.extract_from_pdf(pdf_path, image_dir)

    assert len(slides) == 1
    slide = slides[0]
    assert slide["slide_number"] == 1
    assert slide["title"] == "My Title"
    assert slide["body_text"] == "Body line one"
    assert slide["image_path"]  # a PNG was rendered and linked


def test_extract_from_pdf_preserves_page_order_and_count(tmp_path):
    pdf_path = tmp_path / "deck.pdf"
    pdf_path.write_bytes(make_pdf_bytes(["Alpha", "Beta", "Gamma"]))
    image_dir = tmp_path / "images"
    extract_slides.PROJECT_ROOT = tmp_path

    slides = extract_slides.extract_from_pdf(pdf_path, image_dir)

    assert [s["title"] for s in slides] == ["Alpha", "Beta", "Gamma"]
    assert [s["slide_number"] for s in slides] == [1, 2, 3]
