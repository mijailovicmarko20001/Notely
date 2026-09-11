"""GET/POST /api/exams/*: upload, list, generate (via the job scheduler),
preview/download, PDF export, and the answer-key routes. Job execution
itself is stubbed (a fake stage-11 script under the patched
config.SCRIPTS_DIR, same convention as test_jobs_api.py) -- these tests
pin the HTTP layer, not notely.pipeline.exams's own generation logic
(already covered by tests/test_exams.py)."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pdf_fixtures import make_pdf_bytes  # noqa: E402

from webui import config, jobs  # noqa: E402


def _write_stage11_stub(scripts_dir, sleep=0.0, exit_code=0):
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script = scripts_dir / "11_generate_exam.py"
    script.write_text(
        "import os, sys, time\n"
        f"time.sleep({sleep})\n"
        "os.makedirs('output/exams', exist_ok=True)\n"
        "open('output/exams/exam_01.md', 'w').write('# Exam\\n\\nQ1...')\n"
        "open('output/exams/exam_01_key.md', 'w').write('# Key\\n\\nA1...')\n"
        f"sys.exit({exit_code})\n"
    )
    return script


def _wait_for_job_done(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = jobs.MANAGER.job
        if job and job.get("status") != "running":
            return True
        time.sleep(0.02)
    return False


# --- upload ------------------------------------------------------------------


def test_upload_exams_saves_pdf(client, project_root):
    resp = client.post(
        "/api/exams/upload",
        files=[("files", ("past2023.pdf", make_pdf_bytes(["Question 1"]), "application/pdf"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["saved"] == ["past2023.pdf"]
    assert (config.EXAMS_DIR / "past2023.pdf").exists()


def test_upload_exams_rejects_non_pdf_with_422(client):
    resp = client.post(
        "/api/exams/upload",
        files=[("files", ("past2023.docx", b"not a pdf", "application/octet-stream"))],
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body and "detail" in body


# --- list ----------------------------------------------------------------------


def test_list_exams_empty_project(client):
    resp = client.get("/api/exams")
    assert resp.status_code == 200
    assert resp.json() == {"uploaded": [], "generated": [], "format_cached": False}


def test_list_exams_after_upload_and_generation(client, project_root):
    client.post(
        "/api/exams/upload",
        files=[("files", ("past2023.pdf", make_pdf_bytes(["Q1"]), "application/pdf"))],
    )
    (project_root / "output" / "exams").mkdir(parents=True, exist_ok=True)
    (project_root / "output" / "exams" / "exam_01.md").write_text("# Exam")
    (project_root / "output" / "exams" / "exam_01_key.md").write_text("# Key")

    resp = client.get("/api/exams")
    body = resp.json()
    assert body["uploaded"] == ["past2023.pdf"]
    assert body["generated"] == [{"name": "exam_01", "has_key": True}]


# --- generate (job scheduler) ---------------------------------------------------


def test_generate_starts_a_job_and_returns_job_id(client, project_root):
    _write_stage11_stub(config.SCRIPTS_DIR)

    resp = client.post("/api/exams/generate", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "job_id" in body

    assert _wait_for_job_done()
    assert jobs.MANAGER.job["status"] == "done"
    assert (project_root / "output" / "exams" / "exam_01.md").exists()


def test_generate_409_when_a_job_is_already_running(client, project_root):
    _write_stage11_stub(config.SCRIPTS_DIR, sleep=1.0)

    first = client.post("/api/exams/generate", json={})
    assert first.status_code == 200

    second = client.post("/api/exams/generate", json={})
    assert second.status_code == 409

    jobs.MANAGER.cancel()
    _wait_for_job_done()


def test_generate_rejects_count_below_one(client):
    resp = client.post("/api/exams/generate", json={"count": 0})
    assert resp.status_code == 422


def test_generate_forwards_count_and_force_to_the_task_argv(client, project_root, monkeypatch):
    _write_stage11_stub(config.SCRIPTS_DIR)
    calls = []
    real_start_job = jobs.MANAGER.start_job

    def spy_start_job(tasks):
        calls.append(tasks)
        return real_start_job(tasks)

    monkeypatch.setattr(jobs.MANAGER, "start_job", spy_start_job)

    resp = client.post("/api/exams/generate", json={"count": 3, "questions": 15, "force": True})
    assert resp.status_code == 200
    assert _wait_for_job_done()

    assert len(calls) == 1
    lecture_id, stage, argv = calls[0][0]
    assert lecture_id is None
    assert stage == 11
    assert "--count" in argv and argv[argv.index("--count") + 1] == "3"
    assert "--questions" in argv and argv[argv.index("--questions") + 1] == "15"
    assert "--force" in argv


# --- preview / download ---------------------------------------------------------


def test_get_exam_reports_not_exists_when_nothing_generated(client):
    resp = client.get("/api/exams/exam_01")
    assert resp.status_code == 200
    assert resp.json() == {"exists": False}


def test_get_exam_returns_rendered_html(client, project_root):
    (project_root / "output" / "exams").mkdir(parents=True, exist_ok=True)
    (project_root / "output" / "exams" / "exam_01.md").write_text(
        "# Exam\n\n## Question 1\n\nWhat is $E = mc^2$?\n", encoding="utf-8"
    )

    resp = client.get("/api/exams/exam_01")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["download"] == "/files/exams/exam_01.md"
    assert "$E = mc^2$" in body["html"]


def test_get_exam_key_is_a_separate_route_from_the_paper(client, project_root):
    (project_root / "output" / "exams").mkdir(parents=True, exist_ok=True)
    (project_root / "output" / "exams" / "exam_01.md").write_text("# Exam\n\nQ1...", encoding="utf-8")
    # no key written yet

    paper_resp = client.get("/api/exams/exam_01")
    key_resp = client.get("/api/exams/exam_01/key")

    assert paper_resp.json()["exists"] is True
    assert key_resp.json() == {"exists": False}  # key not gated behind the paper's existence either way


def test_get_exam_key_returns_html_once_generated(client, project_root):
    (project_root / "output" / "exams").mkdir(parents=True, exist_ok=True)
    (project_root / "output" / "exams" / "exam_01_key.md").write_text("# Key\n\nA1...", encoding="utf-8")

    resp = client.get("/api/exams/exam_01/key")
    body = resp.json()
    assert body["exists"] is True
    assert body["download"] == "/files/exams/exam_01_key.md"


# --- name validation / traversal -------------------------------------------------


@pytest.mark.parametrize("bad_name", ["exam_1", "notes", "exam_01.md", "EXAM_01"])
def test_get_exam_rejects_malformed_names(client, bad_name):
    """Names that reach the handler (no literal "/") must be rejected by
    validated_exam_name -- 422, with the standard error body shape."""
    resp = client.get(f"/api/exams/{bad_name}")
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == body["detail"]


def test_get_exam_rejects_traversal_containing_a_path_separator(client):
    # A "/" in the path segment never reaches validated_exam_name at all --
    # Starlette's router 404s it first (the default {name} path converter
    # doesn't match a literal slash). Still a rejection, just at a
    # different layer; this pins that it stays rejected either way.
    resp = client.get("/api/exams/../../etc/passwd")
    assert resp.status_code in (404, 422)


def test_get_exam_pdf_rejects_malformed_name(client):
    resp = client.get("/api/exams/../../etc/passwd/pdf")
    assert resp.status_code in (404, 422)  # either the router 404s the path or validation 422s


# --- pdf export ---------------------------------------------------------------


def test_exam_pdf_404_when_not_generated_yet(client):
    resp = client.get("/api/exams/exam_01/pdf")
    assert resp.status_code == 404


def test_exam_pdf_renders_and_streams(client, project_root, monkeypatch):
    (project_root / "output" / "exams").mkdir(parents=True, exist_ok=True)
    (project_root / "output" / "exams" / "exam_01.md").write_text("# Exam\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        out_path = cmd[cmd.index("--output") + 1]
        with open(out_path, "wb") as f:
            f.write(b"%PDF-1.4 fake\n")

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    from webui import media

    monkeypatch.setattr(media.subprocess, "run", fake_run)

    resp = client.get("/api/exams/exam_01/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


def test_exam_key_pdf_404_when_key_not_generated_yet(client, project_root):
    (project_root / "output" / "exams").mkdir(parents=True, exist_ok=True)
    (project_root / "output" / "exams" / "exam_01.md").write_text("# Exam\n", encoding="utf-8")
    # paper exists, key doesn't
    resp = client.get("/api/exams/exam_01/key/pdf")
    assert resp.status_code == 404
