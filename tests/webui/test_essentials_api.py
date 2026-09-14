"""GET/POST /api/essentials/*: listing, generation (via the job
scheduler), and course/per-lecture preview + PDF export. Job execution
itself is stubbed (fake stage-9/10 scripts under the patched
config.SCRIPTS_DIR, same convention as test_exams_api.py) -- these tests
pin the HTTP layer, not notely.pipeline.essentials's own distillation
logic (already covered by tests/test_essentials.py)."""

import time

from webui import config, jobs


def _write_stage9_stub(scripts_dir, exit_code=0):
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script = scripts_dir / "09_lecture_essentials.py"
    script.write_text(
        "import os, sys\n"
        "lecture_id = sys.argv[1]\n"
        "os.makedirs('output/essentials', exist_ok=True)\n"
        "open(f'output/essentials/{lecture_id}.md', 'w').write('# Essentials\\n\\n- point one')\n"
        f"sys.exit({exit_code})\n"
    )
    return script


def _write_stage10_stub(scripts_dir, exit_code=0):
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script = scripts_dir / "10_course_essentials.py"
    script.write_text(
        "import os, sys\n"
        "os.makedirs('output', exist_ok=True)\n"
        "open('output/essentials.md', 'w').write('# Essentials\\n\\n- course point')\n"
        f"sys.exit({exit_code})\n"
    )
    return script


def _write_notes(project_root, lecture_id):
    notes_dir = project_root / "output" / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / f"{lecture_id}.md").write_text(f"# {lecture_id} notes")


def _wait_for_job_done(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = jobs.MANAGER.job
        if job and job.get("status") != "running":
            return True
        time.sleep(0.02)
    return False


# --- list ----------------------------------------------------------------------


def test_list_essentials_empty_project(client):
    resp = client.get("/api/essentials")
    assert resp.status_code == 200
    body = resp.json()
    # conftest's default project has lecture01/lecture02 in video_urls.json
    assert body["course_essentials"] is False
    assert {lec["id"] for lec in body["lectures"]} == {"lecture01", "lecture02"}
    assert all(not lec["has_notes"] and not lec["has_essentials"] for lec in body["lectures"])


def test_list_essentials_reflects_notes_and_generated_sheets(client, project_root):
    _write_notes(project_root, "lecture01")
    (project_root / "output" / "essentials").mkdir(parents=True, exist_ok=True)
    (project_root / "output" / "essentials" / "lecture01.md").write_text("# Essentials")
    (project_root / "output" / "essentials.md").write_text("# Course essentials")

    resp = client.get("/api/essentials")
    body = resp.json()
    lec01 = next(lec for lec in body["lectures"] if lec["id"] == "lecture01")
    assert lec01 == {"id": "lecture01", "has_notes": True, "has_essentials": True}
    assert body["course_essentials"] is True


# --- generate (job scheduler) ---------------------------------------------------


def test_generate_starts_a_job_over_lectures_with_notes(client, project_root):
    _write_stage9_stub(config.SCRIPTS_DIR)
    _write_stage10_stub(config.SCRIPTS_DIR)
    _write_notes(project_root, "lecture01")
    _write_notes(project_root, "lecture02")

    resp = client.post("/api/essentials/generate", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "job_id" in body

    assert _wait_for_job_done()
    assert jobs.MANAGER.job["status"] == "done"
    assert (project_root / "output" / "essentials" / "lecture01.md").exists()
    assert (project_root / "output" / "essentials" / "lecture02.md").exists()
    assert (project_root / "output" / "essentials.md").exists()


def test_generate_400_when_no_lecture_has_notes_yet(client, project_root):
    resp = client.post("/api/essentials/generate", json={})
    assert resp.status_code == 400


def test_generate_only_includes_lectures_that_have_notes(client, project_root, monkeypatch):
    _write_stage9_stub(config.SCRIPTS_DIR)
    _write_stage10_stub(config.SCRIPTS_DIR)
    _write_notes(project_root, "lecture01")  # lecture02 has no notes

    calls = []
    real_start_job = jobs.MANAGER.start_job

    def spy_start_job(tasks):
        calls.append(tasks)
        return real_start_job(tasks)

    monkeypatch.setattr(jobs.MANAGER, "start_job", spy_start_job)

    resp = client.post("/api/essentials/generate", json={})
    assert resp.status_code == 200
    assert _wait_for_job_done()

    tasks = calls[0]
    lecture_ids = [t[0] for t in tasks if t[1] == 9]
    assert lecture_ids == ["lecture01"]


def test_generate_forwards_force_and_ends_with_a_course_level_task(client, project_root, monkeypatch):
    _write_stage9_stub(config.SCRIPTS_DIR)
    _write_stage10_stub(config.SCRIPTS_DIR)
    _write_notes(project_root, "lecture01")

    calls = []
    real_start_job = jobs.MANAGER.start_job

    def spy_start_job(tasks):
        calls.append(tasks)
        return real_start_job(tasks)

    monkeypatch.setattr(jobs.MANAGER, "start_job", spy_start_job)

    resp = client.post("/api/essentials/generate", json={"force": True})
    assert resp.status_code == 200
    assert _wait_for_job_done()

    tasks = calls[0]
    assert tasks[-1][0] is None
    assert tasks[-1][1] == 10
    assert all("--force" in argv for (_lec, _stage, argv) in tasks)


def test_generate_409_when_a_job_is_already_running(client, project_root):
    _write_stage9_stub(config.SCRIPTS_DIR)
    _write_stage10_stub(config.SCRIPTS_DIR)
    _write_notes(project_root, "lecture01")
    # slow stage 9 so the job is still running when the second request lands
    script = config.SCRIPTS_DIR / "09_lecture_essentials.py"
    script.write_text(
        "import os, sys, time\ntime.sleep(1.0)\nos.makedirs('output/essentials', exist_ok=True)\n"
        "open(f'output/essentials/{sys.argv[1]}.md', 'w').write('# x')\n"
    )

    first = client.post("/api/essentials/generate", json={})
    assert first.status_code == 200

    second = client.post("/api/essentials/generate", json={})
    assert second.status_code == 409

    jobs.MANAGER.cancel()
    _wait_for_job_done()


# --- course preview / download ---------------------------------------------------


def test_get_course_essentials_reports_not_exists_when_nothing_generated(client):
    resp = client.get("/api/essentials/course")
    assert resp.status_code == 200
    assert resp.json() == {"exists": False}


def test_get_course_essentials_returns_rendered_html(client, project_root):
    (project_root / "output" / "essentials.md").write_text(
        "# Essentials\n\nWhat is $E = mc^2$?\n", encoding="utf-8"
    )

    resp = client.get("/api/essentials/course")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["download"] == "/files/essentials.md"
    assert "$E = mc^2$" in body["html"]


# --- per-lecture preview / download ----------------------------------------------


def test_get_lecture_essentials_reports_not_exists_when_nothing_generated(client):
    resp = client.get("/api/essentials/lecture01")
    assert resp.status_code == 200
    assert resp.json() == {"exists": False}


def test_get_lecture_essentials_returns_rendered_html(client, project_root):
    (project_root / "output" / "essentials").mkdir(parents=True, exist_ok=True)
    (project_root / "output" / "essentials" / "lecture01.md").write_text(
        "# Essentials\n\n- point one\n", encoding="utf-8"
    )

    resp = client.get("/api/essentials/lecture01")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["download"] == "/files/essentials/lecture01.md"
    assert "point one" in body["html"]


def test_get_lecture_essentials_rejects_unknown_lecture_id(client):
    # not in video_urls.json -- same trust-boundary check as review/exams routes
    resp = client.get("/api/essentials/lecture99")
    assert resp.status_code == 422


def test_get_lecture_essentials_rejects_traversal_containing_a_path_separator(client):
    resp = client.get("/api/essentials/../../etc/passwd")
    assert resp.status_code in (404, 422)


# --- pdf export ---------------------------------------------------------------


def test_course_essentials_pdf_404_when_not_generated_yet(client):
    resp = client.get("/api/essentials/course/pdf")
    assert resp.status_code == 404


def test_course_essentials_pdf_renders_and_streams(client, project_root, monkeypatch):
    (project_root / "output" / "essentials.md").write_text("# Essentials\n", encoding="utf-8")

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

    resp = client.get("/api/essentials/course/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


def test_lecture_essentials_pdf_404_when_not_generated_yet(client):
    resp = client.get("/api/essentials/lecture01/pdf")
    assert resp.status_code == 404


def test_lecture_essentials_pdf_renders_and_streams(client, project_root, monkeypatch):
    (project_root / "output" / "essentials").mkdir(parents=True, exist_ok=True)
    (project_root / "output" / "essentials" / "lecture01.md").write_text("# Essentials\n", encoding="utf-8")

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

    resp = client.get("/api/essentials/lecture01/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


def test_lecture_essentials_pdf_rejects_unknown_lecture_id(client):
    resp = client.get("/api/essentials/lecture99/pdf")
    assert resp.status_code == 422
