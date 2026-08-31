"""POST/GET /api/review/{lecture_id}: Corrections model validation (A1),
lecture-id validation (S1), and the corrections-apply + job-kickoff flow
including its C2 409-when-busy behavior."""

import json

from webui import jobs


def _write_timeline(project_root, lecture_id, entries):
    path = project_root / "output" / "slide_timelines" / f"{lecture_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"timeline": entries, "notes": []}))


def test_get_review_404_when_no_timeline_yet(client):
    resp = client.get("/api/review/lecture01")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body and "detail" in body


def test_get_review_200_with_timeline(client, project_root):
    _write_timeline(
        project_root,
        "lecture01",
        [
            {"start": 0, "end": 10, "slide_number": 1, "confidence": 0.9},
        ],
    )
    resp = client.get("/api/review/lecture01")
    assert resp.status_code == 200
    assert resp.json()["lecture_id"] == "lecture01"


def test_post_review_rejects_empty_corrections_list(client, project_root):
    _write_timeline(
        project_root,
        "lecture01",
        [
            {"start": 0, "end": 10, "slide_number": 1, "confidence": 0.9},
        ],
    )
    resp = client.post("/api/review/lecture01", json={"corrections": []})
    assert resp.status_code == 422  # Corrections.corrections has min_length=1


def test_post_review_rejects_negative_timestamp(client, project_root):
    _write_timeline(
        project_root,
        "lecture01",
        [
            {"start": 0, "end": 10, "slide_number": 1, "confidence": 0.9},
        ],
    )
    resp = client.post(
        "/api/review/lecture01",
        json={
            "corrections": [{"timestamp": -1, "slide_number": 1}],
        },
    )
    assert resp.status_code == 422  # Correction.timestamp has ge=0


def test_post_review_rejects_missing_timestamp(client, project_root):
    resp = client.post(
        "/api/review/lecture01",
        json={
            "corrections": [{"slide_number": 1}],
        },
    )
    assert resp.status_code == 422


def test_post_review_rejects_traversal_lecture_id(client):
    resp = client.post(
        "/api/review/../../evil",
        json={
            "corrections": [{"timestamp": 1, "slide_number": 1}],
        },
    )
    assert resp.status_code in (404, 422)


def test_post_review_applies_corrections_and_starts_job(client, project_root, monkeypatch):
    _write_timeline(
        project_root,
        "lecture01",
        [
            {"start": 0, "end": 10, "slide_number": 1, "confidence": 0.9},
            {"start": 10, "end": 20, "slide_number": 2, "confidence": 0.4},
        ],
    )
    # Don't actually spin up the scheduler here -- job scheduling correctness
    # is covered by the JobManager threading tests; this test is about the
    # route's own response shape and the corrections having been applied.
    monkeypatch.setattr(jobs.MANAGER, "start_job", lambda tasks: "fixed-job-id")

    resp = client.post(
        "/api/review/lecture01",
        json={
            "corrections": [{"timestamp": 5, "slide_number": 9}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "applied": 1, "timeline_entries": 2, "job_id": "fixed-job-id"}

    data = json.loads((project_root / "output" / "slide_timelines" / "lecture01.json").read_text())
    assert data["timeline"][0]["slide_number"] == 9


def test_post_review_returns_409_when_a_job_is_already_running(client, project_root):
    _write_timeline(
        project_root,
        "lecture01",
        [
            {"start": 0, "end": 10, "slide_number": 1, "confidence": 0.9},
        ],
    )
    with jobs.MANAGER._lock:
        jobs.MANAGER._busy = True
    try:
        resp = client.post(
            "/api/review/lecture01",
            json={
                "corrections": [{"timestamp": 5, "slide_number": 2}],
            },
        )
        assert resp.status_code == 409
        body = resp.json()
        assert "error" in body and "detail" in body
    finally:
        with jobs.MANAGER._lock:
            jobs.MANAGER._busy = False

    # documented trade-off: apply_corrections() runs before the busy check,
    # so the corrected timeline is written even though the job didn't start
    data = json.loads((project_root / "output" / "slide_timelines" / "lecture01.json").read_text())
    assert data["timeline"][0]["slide_number"] == 2
