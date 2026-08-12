"""POST /api/jobs: JobRequest validation (A1), C2's 409-when-busy, and the
"nothing to run" / "lecture_ids and stages required" guard rails."""

from webui import jobs


def test_start_job_rejects_out_of_range_stage(client):
    resp = client.post("/api/jobs", json={
        "lecture_ids": ["lecture01"], "stages": [9],
    })
    assert resp.status_code == 422


def test_start_job_rejects_negative_stage(client):
    resp = client.post("/api/jobs", json={
        "lecture_ids": ["lecture01"], "stages": [-1],
    })
    assert resp.status_code == 422


def test_start_job_rejects_non_list_lecture_ids(client):
    resp = client.post("/api/jobs", json={
        "lecture_ids": "lecture01", "stages": [1],
    })
    assert resp.status_code == 422


def test_start_job_rejects_non_list_stages(client):
    resp = client.post("/api/jobs", json={
        "lecture_ids": ["lecture01"], "stages": "1",
    })
    assert resp.status_code == 422


def test_start_job_requires_stages(client):
    resp = client.post("/api/jobs", json={"lecture_ids": ["lecture01"], "stages": []})
    assert resp.status_code == 400


def test_start_job_requires_lecture_ids_unless_stage_7_only(client):
    resp = client.post("/api/jobs", json={"lecture_ids": [], "stages": [1]})
    assert resp.status_code == 400


def test_start_job_rejects_unknown_lecture_id(client):
    resp = client.post("/api/jobs", json={"lecture_ids": ["lecture99"], "stages": [1]})
    assert resp.status_code == 422


def test_start_job_400_when_nothing_to_run_without_api_key(client, monkeypatch):
    # stage 6 (note generation) is silently skipped by build_tasks without an
    # API key -- if that's the only stage requested, there's nothing to run
    from webui import config
    monkeypatch.setattr(config, "get_api_key", lambda: "")
    resp = client.post("/api/jobs", json={"lecture_ids": ["lecture01"], "stages": [6]})
    assert resp.status_code == 400


def test_start_job_returns_job_id_on_success(client, monkeypatch):
    monkeypatch.setattr(jobs.MANAGER, "start_job", lambda tasks: "abc123")
    resp = client.post("/api/jobs", json={"lecture_ids": ["lecture01"], "stages": [1]})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "job_id": "abc123"}


def test_start_job_stage7_only_does_not_require_lecture_ids(client, monkeypatch):
    monkeypatch.setattr(jobs.MANAGER, "start_job", lambda tasks: "abc123")
    resp = client.post("/api/jobs", json={"lecture_ids": [], "stages": [7]})
    assert resp.status_code == 200


def test_start_job_returns_409_when_already_busy(client):
    with jobs.MANAGER._lock:
        jobs.MANAGER._busy = True
    try:
        resp = client.post("/api/jobs", json={"lecture_ids": ["lecture01"], "stages": [1]})
        assert resp.status_code == 409
        body = resp.json()
        assert "error" in body and "detail" in body
    finally:
        with jobs.MANAGER._lock:
            jobs.MANAGER._busy = False


def test_cancel_returns_409_when_nothing_running(client):
    resp = client.post("/api/jobs/current/cancel")
    assert resp.status_code == 409


def test_current_job_reports_not_busy_initially(client):
    resp = client.get("/api/jobs/current")
    assert resp.status_code == 200
    assert resp.json() == {"job": None, "busy": False}
