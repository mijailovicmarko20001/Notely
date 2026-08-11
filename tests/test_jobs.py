"""webui/jobs.py: the task-list builder and the scheduler's pure
dependency/claim/settled logic (no threads involved in these — the actual
subprocess-running parts aren't covered here, just the bookkeeping that
decides what runs next and in what order)."""

from webui.jobs import JobManager, build_tasks


# --- build_tasks -------------------------------------------------------

def test_build_tasks_basic_stage_list():
    tasks = build_tasks(["lecture01"], [0, 1], options={}, force=False, has_api_key=True)
    assert tasks == [("lecture01", 0, []), ("lecture01", 1, [])]


def test_build_tasks_force_appends_flag_per_task():
    tasks = build_tasks(["lecture01"], [0], options={}, force=True, has_api_key=True)
    assert tasks == [("lecture01", 0, ["--force"])]


def test_build_tasks_stage6_skipped_without_api_key():
    tasks = build_tasks(["lecture01"], [1, 6], options={}, force=False, has_api_key=False)
    stages = [t[1] for t in tasks]
    assert 6 not in stages
    assert 1 in stages


def test_build_tasks_stage6_kept_with_api_key():
    tasks = build_tasks(["lecture01"], [6], options={}, force=False, has_api_key=True)
    assert tasks == [("lecture01", 6, [])]


def test_build_tasks_stage7_runs_once_with_no_lecture_id_and_forced():
    tasks = build_tasks(["lecture01", "lecture02"], [1, 7], options={}, force=False, has_api_key=True)
    stage7_tasks = [t for t in tasks if t[1] == 7]
    assert stage7_tasks == [(None, 7, ["--force"])]  # always forced (07 fails w/o it if output exists)


def test_build_tasks_stage3_options_become_flags():
    options = {"crop": "0.1,0.1,0.8,0.8", "threshold": 0.03, "interval": 2.0}
    tasks = build_tasks(["lecture01"], [3], options=options, force=False, has_api_key=True)
    _, _, extra = tasks[0]
    assert "--crop" in extra and "0.1,0.1,0.8,0.8" in extra
    assert "--threshold" in extra and "0.03" in extra
    assert "--interval" in extra and "2.0" in extra


def test_build_tasks_stage4_options_become_flags_including_min_forward_score():
    options = {
        "ocr_lang": "srp_latn+eng", "margin": 0.15,
        "stay_margin": 0.05, "confidence_threshold": 0.25,
        "min_forward_score": 0.05,
    }
    tasks = build_tasks(["lecture01"], [4], options=options, force=False, has_api_key=True)
    _, _, extra = tasks[0]
    for flag, value in [
        ("--ocr-lang", "srp_latn+eng"), ("--margin", "0.15"),
        ("--stay-margin", "0.05"), ("--confidence-threshold", "0.25"),
        ("--min-forward-score", "0.05"),
    ]:
        assert flag in extra
        assert extra[extra.index(flag) + 1] == value


def test_build_tasks_omits_flags_for_missing_options():
    # options not provided (None/empty) -> flag omitted entirely, letting
    # the stage script fall back to its own default rather than passing
    # e.g. "--margin None"
    tasks = build_tasks(["lecture01"], [4], options={}, force=False, has_api_key=True)
    _, _, extra = tasks[0]
    assert extra == []


def test_build_tasks_multiple_lectures_preserve_order():
    tasks = build_tasks(["lecture01", "lecture02"], [0], options={}, force=False, has_api_key=True)
    assert [t[0] for t in tasks] == ["lecture01", "lecture02"]


# --- JobManager pure scheduling helpers ------------------------------------

def _job_with_statuses(statuses):
    jm = JobManager()
    jm.job = {"tasks": [{"status": s} for s in statuses]}
    return jm


def test_deps_done_true_when_no_earlier_same_lecture_task():
    tasks = [("lecture01", 1, [])]
    jm = _job_with_statuses(["pending"])
    assert jm._deps_done(tasks, 0) is True


def test_deps_done_false_when_earlier_same_lecture_task_not_done():
    tasks = [("lecture01", 1, []), ("lecture01", 2, [])]
    jm = _job_with_statuses(["running", "pending"])
    assert jm._deps_done(tasks, 1) is False


def test_deps_done_true_once_earlier_task_done():
    tasks = [("lecture01", 1, []), ("lecture01", 2, [])]
    jm = _job_with_statuses(["done", "pending"])
    assert jm._deps_done(tasks, 1) is True


def test_deps_done_ignores_other_lectures():
    # lecture02's stage-1 task being pending shouldn't block lecture01's
    # stage-2 task -- deps are per-lecture, not global ordering
    tasks = [("lecture02", 1, []), ("lecture01", 1, []), ("lecture01", 2, [])]
    jm = _job_with_statuses(["pending", "done", "pending"])
    assert jm._deps_done(tasks, 2) is True


def test_claim_next_picks_first_ready_pending_task_in_lane():
    tasks = [("lecture01", 0, []), ("lecture01", 1, [])]
    jm = _job_with_statuses(["pending", "pending"])
    idx = jm._claim_next(tasks, lane_stages={0}, failed_lectures=set())
    assert idx == 0
    assert jm.job["tasks"][0]["status"] == "running"  # claiming marks it running


def test_claim_next_skips_tasks_outside_this_lane():
    # different lectures, so there's no same-lecture dependency between
    # them muddying which one _claim_next is "supposed" to be able to pick
    tasks = [("lecture01", 0, []), ("lecture02", 6, [])]
    jm = _job_with_statuses(["pending", "pending"])
    idx = jm._claim_next(tasks, lane_stages={6}, failed_lectures=set())
    assert idx == 1


def test_claim_next_returns_none_when_dependency_not_ready():
    tasks = [("lecture01", 1, []), ("lecture01", 2, [])]
    jm = _job_with_statuses(["running", "pending"])
    idx = jm._claim_next(tasks, lane_stages={2}, failed_lectures=set())
    assert idx is None  # stage 2 needs stage 1 done first


def test_claim_next_marks_failed_lecture_tasks_skipped():
    # _claim_next notifies other lanes when it skips a task (a lecture that
    # just failed might be blocking them) -- real callers hold self._cond
    # while calling (see _worker), so the test does too.
    tasks = [("lecture01", 2, [])]
    jm = _job_with_statuses(["pending"])
    with jm._cond:
        idx = jm._claim_next(tasks, lane_stages={2}, failed_lectures={"lecture01"})
    assert idx is None
    assert jm.job["tasks"][0]["status"] == "skipped"


def test_lane_settled_true_when_nothing_pending():
    tasks = [("lecture01", 1, [])]
    jm = _job_with_statuses(["done"])
    assert jm._lane_settled(tasks, lane_stages={1}) is True


def test_lane_settled_false_while_pending():
    tasks = [("lecture01", 1, [])]
    jm = _job_with_statuses(["pending"])
    assert jm._lane_settled(tasks, lane_stages={1}) is False


def test_lane_settled_ignores_other_lanes():
    tasks = [("lecture01", 1, []), ("lecture01", 6, [])]
    jm = _job_with_statuses(["done", "pending"])
    assert jm._lane_settled(tasks, lane_stages={1}) is True
