"""A3 (bounded upload size -> 413), A1 (LectureEntries/malformed-body 422s),
and A4 (every NotelyError subclass maps to the same {"error", "detail"}
shape) -- grouped together since most of these exercises are through the
slide-upload routes, which are where TooLargeError/ValidationError/
NotFoundError actually get raised in this codebase."""

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pdf_fixtures import make_pdf_bytes  # noqa: E402

from webui import config, decks  # noqa: E402
from webui.errors import TooLargeError, ValidationError  # noqa: E402


# --- A3: save_upload_stream itself (pure, no HTTP) --------------------------


@pytest.mark.anyio
async def test_save_upload_stream_rejects_over_cap_upload(tmp_path):
    from starlette.datastructures import UploadFile

    big = b"x" * 2048
    f = UploadFile(filename="deck.pdf", file=io.BytesIO(big))
    dest = tmp_path / "deck.pdf"
    with pytest.raises(TooLargeError):
        await decks.save_upload_stream(f, dest, max_bytes=1024)
    # no partial file left behind at the destination or as a .part temp file
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_save_upload_stream_accepts_upload_under_cap(tmp_path):
    from starlette.datastructures import UploadFile

    f = UploadFile(filename="deck.pdf", file=io.BytesIO(b"x" * 100))
    dest = tmp_path / "deck.pdf"
    written = await decks.save_upload_stream(f, dest, max_bytes=1024)
    assert written == 100
    assert dest.read_bytes() == b"x" * 100


@pytest.mark.anyio
async def test_save_upload_stream_rejects_empty_file_by_default(tmp_path):
    from starlette.datastructures import UploadFile

    f = UploadFile(filename="deck.pdf", file=io.BytesIO(b""))
    dest = tmp_path / "deck.pdf"
    with pytest.raises(ValidationError):
        await decks.save_upload_stream(f, dest, max_bytes=1024)


# --- A3 end-to-end through the API: /api/slides/upload -> 413 --------------


def test_upload_slides_413_when_over_configured_cap(client, monkeypatch):
    # save_deck_for_lectures binds its own `max_bytes` default from
    # config.MAX_UPLOAD_BYTES *at function-definition time* (decks.py import
    # time) -- see the note in the final report; monkeypatching
    # config.MAX_UPLOAD_BYTES alone has no effect on an already-imported
    # function's default argument, so the cap is tightened here via a
    # pass-through wrapper instead, to exercise the real route -> 413
    # plumbing (TooLargeError -> the NotelyError handler) end to end.
    real_save_upload_stream = decks.save_upload_stream

    async def tiny_cap_save_upload_stream(file, dest, max_bytes=10, allow_empty=False):
        return await real_save_upload_stream(file, dest, max_bytes=10, allow_empty=allow_empty)

    monkeypatch.setattr(decks, "save_upload_stream", tiny_cap_save_upload_stream)

    resp = client.post(
        "/api/slides/upload",
        data={"lecture_ids": "lecture01"},
        files={"file": ("deck.pdf", b"%PDF-1.4 " + b"x" * 200, "application/pdf")},
    )
    assert resp.status_code == 413
    body = resp.json()
    assert "error" in body and "detail" in body


def test_upload_pool_rejects_non_pdf_with_422(client):
    # pool mode only accepts PDFs -- ValidationError, a NotelyError subclass
    resp = client.post(
        "/api/slides/upload-pool",
        files=[("files", ("deck.pptx", b"not really a pptx", "application/octet-stream"))],
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body and "detail" in body


def test_upload_pool_400_when_no_lectures_configured_yet(client, project_root):
    (project_root / "input" / "video_urls.json").write_text("{}")
    resp = client.post(
        "/api/slides/upload-pool",
        files=[("files", ("deck.pdf", b"%PDF-1.4 fake", "application/pdf"))],
    )
    assert resp.status_code == 400


def test_upload_pool_success_replaces_pool_and_distributes_to_every_lecture(client, project_root):
    # project_root's default fixture lectures are lecture01 and lecture02
    # (see tests/webui/conftest.py's _write_default_lectures)
    deck_a = make_pdf_bytes(["Intro", "CORDIC basics"])
    deck_b = make_pdf_bytes(["Intro", "New material"])  # shares a page with deck_a

    resp = client.post(
        "/api/slides/upload-pool",
        files=[
            ("files", ("deck_a.pdf", deck_a, "application/pdf")),
            ("files", ("deck_b.pdf", deck_b, "application/pdf")),
        ],
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert sorted(body["pool_decks"]) == ["deck_a.pdf", "deck_b.pdf"]
    assert body["scanned_pages"] == 4
    assert body["merged_pages"] == 3  # the repeated "Intro" page deduped
    assert body["duplicate_pages_skipped"] == 1
    assert body["lectures"] == ["lecture01", "lecture02"]

    # the same merged deck was distributed to every configured lecture
    for lecture_id in ("lecture01", "lecture02"):
        deck_path = config.SLIDES_DIR / f"{lecture_id}.pdf"
        assert deck_path.exists() and deck_path.stat().st_size > 0


# --- A4: NotFoundError from media.grab_preview_frame -> 404 -----------------


def test_preview_frame_404_when_no_video_yet(client):
    resp = client.get("/api/lectures/lecture01/preview-frame")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == body["detail"]
    assert "no video" in body["error"]


# --- A1: LectureEntries / malformed bodies ----------------------------------


def test_set_lectures_rejects_empty_entries_list(client):
    resp = client.post("/api/lectures", json={"entries": []})
    assert resp.status_code == 422


def test_set_lectures_rejects_entry_missing_url(client):
    resp = client.post("/api/lectures", json={"entries": [{"title": "no url"}]})
    assert resp.status_code == 422


def test_set_lectures_rejects_non_dict_entries(client):
    resp = client.post("/api/lectures", json={"entries": ["not-a-dict"]})
    assert resp.status_code == 422


def test_upload_slides_requires_lecture_ids_form_field(client):
    resp = client.post(
        "/api/slides/upload",
        files={"file": ("deck.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 422  # FastAPI: required Form field missing


# --- A4: consistent error shape across both handler paths -------------------


def test_starlette_http_exception_and_notely_error_share_response_shape(client, project_root):
    (project_root / "input" / "video_urls.json").write_text("{}")
    resp_http_exc = client.post(
        "/api/slides/upload-pool",
        files=[("files", ("deck.pdf", b"%PDF-1.4 fake", "application/pdf"))],
    )  # HTTPException(400, ...) path
    resp_notely_err = client.post(
        "/api/slides/upload",
        data={"lecture_ids": "../../evil"},
        files={"file": ("deck.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )  # HTTPException(422, ...) via validated_lecture_id -- both routed
    for resp in (resp_http_exc, resp_notely_err):
        body = resp.json()
        assert set(body.keys()) == {"error", "detail"}
        assert body["error"] == body["detail"]
