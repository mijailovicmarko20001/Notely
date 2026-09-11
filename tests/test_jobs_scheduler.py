"""JobManager end-to-end scheduling tests (T2): real threads, real
subprocesses -- just tiny stand-in "stage scripts" instead of the actual
pipeline stages, so the real two-lane scheduler in webui/jobs.py runs in
milliseconds.

Note on the fixture: the brief for this task described an existing
tmp_path-based project-root fixture with stub stage scripts already living
alongside tests/test_jobs.py's 69 pure-logic tests. As of this writing,
tests/test_jobs.py has no such fixture (just JobManager()/build_tasks()
called directly, no monkeypatching, no scripts dir) -- so this file adds
its own, rather than editing test_jobs.py (which stays untouched and
green). Only webui.config's own constants need patching (Phase 6:
webui/jobs.py and webui/progress.py now read config.X through the module
reference instead of each binding its own `from .config import X` copy at
import time, so there's exactly one place to redirect)."""

import json
import time

import pytest

from webui import config, jobs

# --- fixture: tmp project root + stub stage scripts -------------------------

STAGE_ARTIFACT_REL = {
    0: "input/videos/{lec}.mp4",
    1: "output/transcripts/{lec}.json",
    2: "output/slides_extracted/{lec}.json",
    3: "output/frame_events/{lec}.json",
    4: "output/slide_timelines/{lec}.json",
    5: "output/segmented_transcripts/{lec}.json",
    6: "output/notes/{lec}.md",
    7: "output/study_guide.md",
    11: "output/exams/exam_01.md",
}


@pytest.fixture()
def project(tmp_path, monkeypatch):
    root = tmp_path
    (root / "input" / "videos").mkdir(parents=True)
    (root / "input" / "slides").mkdir(parents=True)
    (root / "output" / "logs").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "input" / "video_urls.json").write_text(
        json.dumps(
            {
                "lecture01": "https://youtu.be/aaaaaaaaaaa",
                "lecture02": "https://youtu.be/bbbbbbbbbbb",
            }
        )
    )

    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.setattr(config, "SCRIPTS_DIR", root / "scripts")
    monkeypatch.setattr(config, "INPUT_DIR", root / "input")
    monkeypatch.setattr(config, "OUTPUT_DIR", root / "output")
    monkeypatch.setattr(config, "LOGS_DIR", root / "output" / "logs")
    monkeypatch.setattr(config, "VIDEOS_DIR", root / "input" / "videos")
    monkeypatch.setattr(config, "ENV_PATH", root / ".env")

    # Real-process leak guard: scripts/04_match_frames_to_slides.py calls
    # load_dotenv(PROJECT_ROOT / ".env") at *module import time* (so its
    # --ocr-lang default picks up .env), not inside a function -- merely
    # importing it (e.g. via conftest.load_stage in another test module's
    # own module-level code, which several files in this suite do) loads
    # the real repo's .env into the real os.environ for the rest of the
    # pytest process, regardless of which specific test triggered the
    # import. This project's real .env has WHISPER_BACKEND=mlx, which
    # config.stage_env() (real os.environ + dotenv_values(ENV_PATH)) would
    # otherwise pick straight up here, since nothing in this fixture ever
    # writes a project/.env for these tests to override it with. Delete
    # it explicitly rather than relying on it happening to be unset.
    monkeypatch.delenv("WHISPER_BACKEND", raising=False)

    return root


def write_stub(
    scripts_dir,
    stage,
    *,
    name="stub",
    exit_code=0,
    sleep=0.0,
    write_artifact=True,
    log_path=None,
    stdout_lines=None,
    fail_for_lecture=None,
):
    """A stage script that: optionally sleeps, optionally logs
    "<time> start/end <stage> <lecture_id>" lines to `log_path` (append
    mode -- used to verify ordering/overlap after the job finishes),
    optionally prints progress lines, writes the expected artifact
    (progress.stage_artifact's path, computed relative to cwd since the
    real scheduler runs stage scripts with cwd=PROJECT_ROOT), and exits.

    `stage_script()` resolves one script per stage number regardless of
    which lecture it's invoked for (it globs `{stage:02d}_*.py`), so a
    per-lecture pass/fail script needs `fail_for_lecture`: the *same*
    script exits 1 (and skips writing its artifact) only when invoked for
    that lecture id, succeeding normally for every other lecture.
    """
    rel = STAGE_ARTIFACT_REL[stage]
    script = scripts_dir / f"{stage:02d}_{name}.py"
    lines = [
        "import sys, os, time",
        "lecture_id = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else None",
    ]
    if fail_for_lecture is not None:
        lines.append(f"_fail = lecture_id == {fail_for_lecture!r}")
    else:
        lines.append("_fail = False")
    if log_path is not None:
        lines.append(
            f"open({str(log_path)!r}, 'a').write(f'{{time.time()}}|start|{stage}|{{lecture_id}}\\n')"
        )
    if stdout_lines:
        for line in stdout_lines:
            lines.append(f"print({line!r}); sys.stdout.flush()")
    if sleep:
        lines.append(f"time.sleep({sleep})")
    if write_artifact:
        lines.append("if not _fail:")
        lines.append(f"    path = {rel!r}.format(lec=lecture_id)")
        lines.append("    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)")
        lines.append("    with open(path, 'w') as f: f.write('stub\\n')")
    if log_path is not None:
        lines.append(f"open({str(log_path)!r}, 'a').write(f'{{time.time()}}|end|{stage}|{{lecture_id}}\\n')")
    lines.append(f"sys.exit(1 if _fail else {exit_code})")
    script.write_text("\n".join(lines) + "\n")
    return script


def wait_until(predicate, timeout=5.0, interval=0.01):
    deadline = time.time() + timeout
    ok = predicate()
    while not ok and time.time() < deadline:
        time.sleep(interval)
        ok = predicate()
    return ok


def wait_for_job_done(manager, timeout=5.0):
    return wait_until(lambda: (manager.job or {}).get("status") not in (None, "running"), timeout=timeout)


def read_log(path):
    """[(ts, event, stage, lecture_id), ...] sorted by timestamp."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        ts, event, stage, lec = line.split("|")
        rows.append((float(ts), event, int(stage), None if lec == "None" else lec))
    return sorted(rows)


# --- lane parallelism --------------------------------------------------------


def test_different_lanes_run_concurrently(project):
    log = project / "order.log"
    write_stub(config.SCRIPTS_DIR, 0, sleep=0.3, log_path=log)  # net lane
    write_stub(config.SCRIPTS_DIR, 6, sleep=0.3, log_path=log)  # api lane

    manager = jobs.JobManager()
    tasks = [("lecture01", 0, []), ("lecture02", 6, [])]
    manager.start_job(tasks)
    assert wait_for_job_done(manager, timeout=5)

    # assert on the recorded start/end windows instead of total wall-clock
    # elapsed -- a wall-clock threshold has to leave slack for subprocess
    # spawn overhead, which made this flaky under load; the two lanes'
    # windows overlapping is what "ran concurrently" actually means.
    rows = read_log(log)
    starts = {st: ts for ts, ev, st, _ in rows if ev == "start"}
    ends = {st: ts for ts, ev, st, _ in rows if ev == "end"}
    assert starts[0] < ends[6] and starts[6] < ends[0], (
        f"lanes ran serially instead of concurrently: "
        f"stage0={starts[0]:.3f}-{ends[0]:.3f} stage6={starts[6]:.3f}-{ends[6]:.3f}"
    )
    assert manager.job["status"] == "done"


def test_same_lane_tasks_run_one_at_a_time(project):
    # both stage-1 tasks land in the CPU lane, which only runs one task at
    # once -- unlike the different-lane case above, this pair must NOT
    # overlap
    log = project / "order.log"
    write_stub(config.SCRIPTS_DIR, 1, sleep=0.2, log_path=log)

    manager = jobs.JobManager()
    tasks = [("lecture01", 1, []), ("lecture02", 1, [])]
    manager.start_job(tasks)
    assert wait_for_job_done(manager, timeout=5)

    # same reasoning as test_different_lanes_run_concurrently above: check
    # the recorded windows don't overlap, rather than inferring
    # non-overlap from a minimum total wall-clock elapsed.
    rows = read_log(log)
    starts = {lec: ts for ts, ev, st, lec in rows if ev == "start"}
    ends = {lec: ts for ts, ev, st, lec in rows if ev == "end"}
    assert ends["lecture01"] <= starts["lecture02"] or ends["lecture02"] <= starts["lecture01"], (
        f"same-lane tasks overlapped: lecture01={starts['lecture01']:.3f}-{ends['lecture01']:.3f} "
        f"lecture02={starts['lecture02']:.3f}-{ends['lecture02']:.3f}"
    )


def test_stage11_task_runs_to_completion_on_the_api_lane(project):
    """Stage 11 (practice exams) is a standalone, course-level stage built
    outside notely.runner.build_tasks (see webui/routes/exams.py) and
    handed to MANAGER.start_job() directly -- unlike stages 0-7, it isn't
    covered by build_tasks/PER_LECTURE_STAGES at all. _run()'s lane
    construction has to know about it explicitly (added to the "api" lane,
    alongside stage 6) or a stage-11 task would sit "pending" forever: no
    lane worker's `stage in lane_stages` check would ever match it, and
    _lane_settled only looks at each lane's own stage set, so the job
    would silently report "done" with the task never having run.
    start_job() itself would also KeyError on progress.STAGE_NAMES[11]
    before any of that if STAGE_NAMES weren't extended past
    MAX_PIPELINE_STAGE -- this test covers both."""
    write_stub(config.SCRIPTS_DIR, 11, name="generate_exam", exit_code=0)

    manager = jobs.JobManager()
    manager.start_job([(None, 11, [])])
    assert wait_for_job_done(manager, timeout=5)

    assert manager.job["tasks"][0]["status"] == "done"
    assert manager.job["status"] == "done"
    assert (project / "output" / "exams" / "exam_01.md").exists()


# --- dependency ordering per lecture -----------------------------------------


def test_later_stage_waits_for_earlier_stage_same_lecture(project):
    # stage 0 (net lane) and stage 1 (cpu lane) for the SAME lecture: since
    # they're on different lanes, only _deps_done (not lane serialization)
    # can be responsible for stage 1 waiting on stage 0.
    log = project / "order.log"
    write_stub(config.SCRIPTS_DIR, 0, sleep=0.2, log_path=log)
    write_stub(config.SCRIPTS_DIR, 1, sleep=0.0, log_path=log)

    manager = jobs.JobManager()
    tasks = [("lecture01", 0, []), ("lecture01", 1, [])]
    manager.start_job(tasks)
    assert wait_for_job_done(manager, timeout=5)

    rows = read_log(log)
    stage0_end = next(ts for ts, ev, st, _ in rows if st == 0 and ev == "end")
    stage1_start = next(ts for ts, ev, st, _ in rows if st == 1 and ev == "start")
    assert stage1_start >= stage0_end


def test_independent_lectures_are_not_serialized_by_each_others_deps(project):
    write_stub(config.SCRIPTS_DIR, 1, sleep=0.0)
    write_stub(config.SCRIPTS_DIR, 2, sleep=0.0)

    manager = jobs.JobManager()
    # lecture02's stage-1 is pending -- must not block lecture01's stage-2
    tasks = [("lecture02", 1, []), ("lecture01", 1, []), ("lecture01", 2, [])]
    manager.start_job(tasks)
    assert wait_for_job_done(manager, timeout=5)
    assert manager.job["status"] == "done"
    assert all(t["status"] == "done" for t in manager.job["tasks"])


# --- failure skips downstream -------------------------------------------------


def test_failure_skips_downstream_tasks_for_same_lecture_only(project):
    write_stub(config.SCRIPTS_DIR, 1, fail_for_lecture="lecture01")
    write_stub(config.SCRIPTS_DIR, 2, exit_code=0)

    manager = jobs.JobManager()
    tasks = [
        ("lecture01", 1, []),
        ("lecture01", 2, []),  # must be skipped
        ("lecture02", 1, []),  # unaffected
    ]
    manager.start_job(tasks)
    assert wait_for_job_done(manager, timeout=5)

    by_lecture = {(t["lecture_id"], t["stage"]): t for t in manager.job["tasks"]}
    assert by_lecture[("lecture01", 1)]["status"] == "failed"
    assert by_lecture[("lecture01", 2)]["status"] == "skipped"
    assert by_lecture[("lecture02", 1)]["status"] == "done"
    assert manager.job["status"] == "failed"

    # the skipped stage must never have actually run
    assert not (config.OUTPUT_DIR / "slides_extracted" / "lecture01.json").exists()


def test_missing_artifact_marks_stage_blocked_not_done(project):
    # exit 0 but *doesn't* write the artifact -- e.g. a stage that silently
    # skipped because its input was missing
    write_stub(config.SCRIPTS_DIR, 1, exit_code=0, write_artifact=False)

    manager = jobs.JobManager()
    manager.start_job([("lecture01", 1, [])])
    assert wait_for_job_done(manager, timeout=5)
    assert manager.job["tasks"][0]["status"] == "blocked"


# --- cancel semantics ---------------------------------------------------------


def test_cancel_stops_pending_and_running_tasks(project):
    write_stub(config.SCRIPTS_DIR, 1, sleep=1.0)
    write_stub(config.SCRIPTS_DIR, 2, sleep=0.0)

    manager = jobs.JobManager()
    manager.start_job([("lecture01", 1, []), ("lecture01", 2, [])])
    assert wait_until(lambda: manager.job["tasks"][0]["status"] == "running", timeout=2)

    manager.cancel()
    assert wait_for_job_done(manager, timeout=5)

    assert manager.job["status"] == "cancelled"
    assert manager.job["tasks"][0]["status"] == "cancelled"
    # stage 2 was still pending when cancel() ran -- never got to claim it
    assert manager.job["tasks"][1]["status"] in ("cancelled",)


def test_cancel_during_final_assembly_terminates_the_stage7_process(project):
    """C5: cancel arriving *during* stage 7 must actually kill it, not just
    flip a flag that the already-running assembly process ignores.

    Can't poll `job["tasks"][0]["status"] == "running"` here the way the
    other cancel tests do: `_run()`'s final-tasks loop (jobs.py) calls
    `_run_task("final", ...)` directly, without ever setting the task's
    status to "running" first the way `_claim_next` does for lane-claimed
    tasks -- see this file's bottom section ("known gap") for a dedicated
    regression test pinning that down. So this test polls the emitted
    `stage_start` event instead, which *is* recorded correctly.
    """
    write_stub(config.SCRIPTS_DIR, 7, sleep=2.0)

    manager = jobs.JobManager()
    started = time.time()
    manager.start_job([(None, 7, [])])
    assert wait_until(lambda: any(e["type"] == "stage_start" for e in manager.events_since(0)), timeout=2)

    manager.cancel()
    assert wait_for_job_done(manager, timeout=5)
    elapsed = time.time() - started

    assert manager.job["status"] == "cancelled"
    assert manager.job["tasks"][0]["status"] == "cancelled"
    # proves the process was actually terminated, not left to run its full
    # 2s sleep to completion
    assert elapsed < 1.5, f"stage 7 proc wasn't terminated promptly ({elapsed:.2f}s)"
    assert not (config.OUTPUT_DIR / "study_guide.md").exists()


def test_cancel_before_final_assembly_starts_marks_it_cancelled_without_running(project):
    write_stub(config.SCRIPTS_DIR, 1, sleep=0.3)
    write_stub(config.SCRIPTS_DIR, 7, sleep=0.0)

    manager = jobs.JobManager()
    manager.start_job([("lecture01", 1, []), (None, 7, [])])
    assert wait_until(lambda: manager.job["tasks"][0]["status"] == "running", timeout=2)
    manager.cancel()
    assert wait_for_job_done(manager, timeout=5)

    final_task = manager.job["tasks"][1]
    assert final_task["status"] == "cancelled"
    assert not (config.OUTPUT_DIR / "study_guide.md").exists()


# --- final-task status while executing ---------------------------------------


def test_final_task_status_shows_running_while_executing(project):
    """Regression test for a gap found while writing this suite: `_run()`'s
    final-tasks loop used to call `_run_task("final", ...)` directly without
    ever setting `task["status"] = "running"` first, unlike `_claim_next`
    (used by the lane workers), which does. That meant a client polling
    `GET /api/jobs/current` (or watching `task["status"]`) during stage 7 saw
    "pending" right up until it flipped straight to "done"/"failed"/
    "cancelled" -- it couldn't tell "assembly is running" from "still queued
    behind the lanes". `_run()` now flips the final task to "running" before
    calling `_run_task("final", ...)`, matching `_claim_next`.
    """
    write_stub(config.SCRIPTS_DIR, 7, sleep=0.2)
    manager = jobs.JobManager()
    manager.start_job([(None, 7, [])])

    assert wait_until(lambda: any(e["type"] == "stage_start" for e in manager.events_since(0)), timeout=2), (
        "stage 7 never started"
    )
    # the task is executing right now (proc registered, event fired) and the
    # status field reflects that instead of still claiming "pending"
    assert manager.job["tasks"][0]["status"] == "running"

    assert wait_for_job_done(manager, timeout=5)
    assert manager.job["tasks"][0]["status"] == "done"


# --- snapshot consistency under concurrent polling (C1 regression) ----------


def test_snapshot_survives_concurrent_polling_without_torn_json(project):
    import threading

    n_lectures = 5
    write_stub(config.SCRIPTS_DIR, 1, sleep=0.03)
    write_stub(config.SCRIPTS_DIR, 2, sleep=0.03)
    write_stub(config.SCRIPTS_DIR, 3, sleep=0.03)

    manager = jobs.JobManager()
    tasks = []
    for i in range(1, n_lectures + 1):
        lec = f"lecture{i:02d}"
        tasks += [(lec, 1, []), (lec, 2, []), (lec, 3, [])]

    errors = []
    stop = threading.Event()

    def hammer():
        while not stop.is_set():
            try:
                snap = manager.snapshot()
                if snap is not None:
                    json.dumps(snap)  # would raise / produce garbage on a torn dict
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

    pollers = [threading.Thread(target=hammer, daemon=True) for _ in range(8)]
    for p in pollers:
        p.start()

    manager.start_job(tasks)
    assert wait_for_job_done(manager, timeout=10)

    stop.set()
    for p in pollers:
        p.join(timeout=2)

    assert errors == [], f"snapshot()/json.dumps() raised under concurrent polling: {errors[:3]}"
    assert manager.job["status"] == "done"
    assert all(t["status"] == "done" for t in manager.job["tasks"])


def test_events_since_survives_concurrent_polling(project):
    import threading

    write_stub(config.SCRIPTS_DIR, 1, sleep=0.02)
    manager = jobs.JobManager()
    tasks = [(f"lecture{i:02d}", 1, []) for i in range(1, 4)]

    errors = []
    stop = threading.Event()

    def hammer():
        while not stop.is_set():
            try:
                manager.events_since(0)
            except Exception as e:  # pragma: no cover
                errors.append(e)

    pollers = [threading.Thread(target=hammer, daemon=True) for _ in range(4)]
    for p in pollers:
        p.start()

    manager.start_job(tasks)
    assert wait_for_job_done(manager, timeout=5)
    stop.set()
    for p in pollers:
        p.join(timeout=2)

    assert errors == []
