"""scripts/06_generate_notes.py::load_frame_image_b64 -- loading/downscaling/
encoding the actual on-screen slide capture for the (opt-in) vision path,
so live annotations the deck file never had can reach note generation.
See DOCUMENTATION.md / TODO.md for the full feature."""

import base64
import io

from conftest import load_stage

m6 = load_stage("06_generate_notes.py")


def _make_png(tmp_path, name, size, color=(120, 140, 160)):
    from PIL import Image
    path = tmp_path / name
    Image.new("RGB", size, color=color).save(path)
    return path


def test_load_frame_image_returns_none_for_missing_file(tmp_path):
    m6.PROJECT_ROOT = tmp_path
    assert m6.load_frame_image_b64("does_not_exist.png") is None


def test_load_frame_image_small_image_unchanged_size(tmp_path):
    _make_png(tmp_path, "small.png", (640, 480))
    m6.PROJECT_ROOT = tmp_path
    b64 = m6.load_frame_image_b64("small.png")
    assert b64 is not None
    from PIL import Image
    decoded = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert decoded.size == (640, 480)


def test_load_frame_image_large_image_downscaled_preserving_aspect(tmp_path):
    _make_png(tmp_path, "big.png", (3840, 2160))
    m6.PROJECT_ROOT = tmp_path
    b64 = m6.load_frame_image_b64("big.png")
    from PIL import Image
    decoded = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert max(decoded.size) <= m6.FRAME_IMAGE_MAX_DIM
    assert round(decoded.size[0] / decoded.size[1], 3) == round(3840 / 2160, 3)


def test_load_frame_image_result_decodes_as_valid_png(tmp_path):
    _make_png(tmp_path, "a.png", (800, 600))
    m6.PROJECT_ROOT = tmp_path
    b64 = m6.load_frame_image_b64("a.png")
    from PIL import Image
    decoded = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert decoded.format == "PNG"


def test_load_frame_image_handles_corrupt_file_gracefully(tmp_path):
    bad_path = tmp_path / "corrupt.png"
    bad_path.write_bytes(b"not actually a png")
    m6.PROJECT_ROOT = tmp_path
    assert m6.load_frame_image_b64("corrupt.png") is None
