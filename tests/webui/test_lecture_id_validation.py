"""S1: lecture-id validation is the trust boundary for every filesystem path
built from user input. `routes/common.py::validated_lecture_id` centralizes
it; exercise it both directly and through the routes that call it."""

import pytest
from fastapi import HTTPException

from webui.routes.common import validated_lecture_id


def test_validated_lecture_id_accepts_known_lecture():
    assert validated_lecture_id("lecture01") == "lecture01"


def test_validated_lecture_id_rejects_path_traversal_shape():
    with pytest.raises(HTTPException) as exc_info:
        validated_lecture_id("../../evil")
    assert exc_info.value.status_code == 422


def test_validated_lecture_id_rejects_absolute_path_shape():
    with pytest.raises(HTTPException) as exc_info:
        validated_lecture_id("/etc/passwd")
    assert exc_info.value.status_code == 422


def test_validated_lecture_id_rejects_wellformed_but_unknown_id():
    # matches LECTURE_ID_RE but isn't in video_urls.json -- membership check
    with pytest.raises(HTTPException) as exc_info:
        validated_lecture_id("lecture99")
    assert exc_info.value.status_code == 422


def test_validated_lecture_id_rejects_case_mismatch():
    with pytest.raises(HTTPException):
        validated_lecture_id("LECTURE01")


def test_validated_lecture_id_rejects_single_digit_suffix():
    # LECTURE_ID_RE requires \d{2,}
    with pytest.raises(HTTPException):
        validated_lecture_id("lecture1")


# --- through routes that build filesystem paths from the id ----------------

def test_get_review_rejects_traversal_lecture_id(client):
    resp = client.get("/api/review/lecture99")
    assert resp.status_code == 422
    assert "error" in resp.json()


def test_upload_slides_rejects_traversal_in_lecture_ids_form_field(client):
    # This was the original S1 finding: a comma-separated form field, not a
    # path parameter, so Starlette's "no slash in a path segment" guard
    # never applied here in the first place.
    resp = client.post(
        "/api/slides/upload",
        data={"lecture_ids": "../../evil"},
        files={"file": ("deck.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 422


def test_upload_slides_rejects_when_any_id_in_list_is_invalid(client):
    resp = client.post(
        "/api/slides/upload",
        data={"lecture_ids": "lecture01,../../evil"},
        files={"file": ("deck.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 422


def test_preview_frame_rejects_traversal_lecture_id(client):
    resp = client.get("/api/lectures/../../evil/preview-frame")
    # Starlette normalizes ".." out of the URL path before routing even
    # sees it, so this either 404s (no matching route) or lands in
    # validated_lecture_id and 422s -- either way it must never reach
    # media.grab_preview_frame with a traversal-shaped id.
    assert resp.status_code in (404, 422)
