"""webui/exams.py: pure logic (no HTTP) for exam-name validation, upload
persistence, and state listing -- same separation/testing convention as
tests/webui/test_uploads_and_errors.py's coverage of webui/decks.py."""

import io

import pytest

from webui import config, exams
from webui.errors import ValidationError


@pytest.fixture(autouse=True)
def _project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXAMS_DIR", tmp_path / "input" / "exams")
    monkeypatch.setattr(config, "OUTPUT_EXAMS_DIR", tmp_path / "output" / "exams")
    return tmp_path


# --- validated_exam_name -----------------------------------------------------


def test_validated_exam_name_accepts_well_formed_names():
    assert exams.validated_exam_name("exam_01") == "exam_01"
    assert exams.validated_exam_name("exam_12") == "exam_12"


@pytest.mark.parametrize(
    "bad",
    ["../../etc/passwd", "exam_1", "exam_01.md", "exam_01/../../x", "", "exam_01 ", "EXAM_01", "notes"],
)
def test_validated_exam_name_rejects_malformed_or_traversal_names(bad):
    with pytest.raises(ValidationError):
        exams.validated_exam_name(bad)


# --- save_exam_uploads --------------------------------------------------------


@pytest.mark.anyio
async def test_save_exam_uploads_writes_pdfs_to_exams_dir(tmp_path):
    from starlette.datastructures import UploadFile

    f = UploadFile(filename="past2023.pdf", file=io.BytesIO(b"%PDF-1.4 fake"))
    saved = await exams.save_exam_uploads([f])

    assert saved == ["past2023.pdf"]
    assert (config.EXAMS_DIR / "past2023.pdf").read_bytes() == b"%PDF-1.4 fake"


@pytest.mark.anyio
async def test_save_exam_uploads_rejects_non_pdf():
    from starlette.datastructures import UploadFile

    f = UploadFile(filename="past2023.docx", file=io.BytesIO(b"not a pdf"))
    with pytest.raises(ValidationError):
        await exams.save_exam_uploads([f])


@pytest.mark.anyio
async def test_save_exam_uploads_is_additive_not_replacing(tmp_path):
    """Unlike decks.save_pool_uploads (replaces the whole pool), each
    upload call adds to the existing set of past exams -- there's no
    "current course" staleness concept for format-template examples."""
    from starlette.datastructures import UploadFile

    f1 = UploadFile(filename="past2023.pdf", file=io.BytesIO(b"%PDF-1.4 a"))
    await exams.save_exam_uploads([f1])

    f2 = UploadFile(filename="past2024.pdf", file=io.BytesIO(b"%PDF-1.4 b"))
    await exams.save_exam_uploads([f2])

    assert sorted(p.name for p in config.EXAMS_DIR.glob("*.pdf")) == ["past2023.pdf", "past2024.pdf"]


@pytest.mark.anyio
async def test_save_exam_uploads_reupload_same_name_overwrites(tmp_path):
    from starlette.datastructures import UploadFile

    f1 = UploadFile(filename="past2023.pdf", file=io.BytesIO(b"%PDF-1.4 old"))
    await exams.save_exam_uploads([f1])
    f2 = UploadFile(filename="past2023.pdf", file=io.BytesIO(b"%PDF-1.4 new"))
    await exams.save_exam_uploads([f2])

    assert (config.EXAMS_DIR / "past2023.pdf").read_bytes() == b"%PDF-1.4 new"
    assert len(list(config.EXAMS_DIR.glob("*.pdf"))) == 1


# --- list_exam_state -----------------------------------------------------------


def test_list_exam_state_empty_project():
    assert exams.list_exam_state() == {"uploaded": [], "generated": [], "format_cached": False}


def test_list_exam_state_reports_uploaded_and_generated(tmp_path):
    config.EXAMS_DIR.mkdir(parents=True)
    (config.EXAMS_DIR / "past2023.pdf").write_bytes(b"%PDF-1.4")

    config.OUTPUT_EXAMS_DIR.mkdir(parents=True)
    (config.OUTPUT_EXAMS_DIR / "exam_01.md").write_text("# Exam")
    (config.OUTPUT_EXAMS_DIR / "exam_01_key.md").write_text("# Key")
    (config.OUTPUT_EXAMS_DIR / "exam_02.md").write_text("# Exam 2")  # no key yet
    (config.OUTPUT_EXAMS_DIR / "_format.json").write_text("{}")

    state = exams.list_exam_state()

    assert state["uploaded"] == ["past2023.pdf"]
    assert state["generated"] == [
        {"name": "exam_01", "has_key": True},
        {"name": "exam_02", "has_key": False},
    ]
    assert state["format_cached"] is True


def test_list_exam_state_does_not_list_key_files_as_papers(tmp_path):
    config.OUTPUT_EXAMS_DIR.mkdir(parents=True)
    (config.OUTPUT_EXAMS_DIR / "exam_01_key.md").write_text("# Key (paper missing)")

    state = exams.list_exam_state()

    assert state["generated"] == []
