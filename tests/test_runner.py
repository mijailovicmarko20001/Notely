"""notely.runner.build_tasks -- the canonical import path (Phase 6). Full
behavioral coverage already lives in tests/test_jobs.py (which imports it
via `from webui.jobs import build_tasks`, a re-export of the same
function); this just pins that the new home is importable and wired up
correctly, and that it's driven by the stage registry rather than a
hardcoded "!= 7" check."""

from notely.runner import build_tasks
from notely.stages import MAX_PIPELINE_STAGE


def test_build_tasks_is_the_same_object_webui_jobs_reexports():
    from webui.jobs import build_tasks as webui_build_tasks

    assert webui_build_tasks is build_tasks


def test_stage_max_pipeline_stage_runs_once_with_no_lecture_id():
    tasks = build_tasks(
        ["lecture01", "lecture02"], [1, MAX_PIPELINE_STAGE], {}, force=False, has_api_key=True
    )
    final = [t for t in tasks if t[1] == MAX_PIPELINE_STAGE]
    assert final == [(None, MAX_PIPELINE_STAGE, ["--force"])]


def test_stage_max_pipeline_stage_omitted_when_not_requested():
    tasks = build_tasks(["lecture01"], [1], {}, force=False, has_api_key=True)
    assert all(stage != MAX_PIPELINE_STAGE for _, stage, _ in tasks)
