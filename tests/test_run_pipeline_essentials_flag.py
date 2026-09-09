"""run_pipeline.py's --essentials flag: after the main stage range (and
--assemble, if requested), run stage 9 per lecture then stage 10
course-level -- both standalone, invoked directly the same way --assemble
invokes stage 7 (see notely/stages.py, scripts/09_lecture_essentials.py,
scripts/10_course_essentials.py).

run_stage() itself is monkeypatched to a recording stub, same convention
as test_run_pipeline_stage_flags.py -- these tests pin *routing* (which
stage numbers run, in what order, skipped when), not each stage script's
own argument parsing."""

import sys

import pytest

from conftest import load_stage

rp = load_stage("run_pipeline.py")


def _stub_run_stage(monkeypatch, fail_stages=frozenset()):
    calls = []

    def fake_run_stage(stage_num, lecture_id=None, extra_args=None):
        calls.append((stage_num, lecture_id, extra_args))
        return stage_num not in fail_stages

    monkeypatch.setattr(rp, "run_stage", fake_run_stage)
    return calls


def _write_two_lecture_course(tmp_path, monkeypatch):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "video_urls.json").write_text(
        '{"lecture01": "https://youtu.be/a", "lecture02": "https://youtu.be/b"}'
    )
    monkeypatch.setattr(rp, "get_project_root", lambda: tmp_path)


def test_essentials_flag_runs_stage9_per_lecture_then_stage10(tmp_path, monkeypatch):
    _write_two_lecture_course(tmp_path, monkeypatch)
    calls = _stub_run_stage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--all", "--essentials"])

    rp.main()

    essentials_calls = [c for c in calls if c[0] in (9, 10)]
    assert essentials_calls == [
        (9, "lecture01", []),
        (9, "lecture02", []),
        (10, None, []),
    ]


def test_essentials_flag_off_by_default(tmp_path, monkeypatch):
    _write_two_lecture_course(tmp_path, monkeypatch)
    calls = _stub_run_stage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--all"])

    rp.main()

    assert all(c[0] not in (9, 10) for c in calls)


def test_essentials_flag_forwards_force(tmp_path, monkeypatch):
    _write_two_lecture_course(tmp_path, monkeypatch)
    calls = _stub_run_stage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--all", "--essentials", "--force"])

    rp.main()

    essentials_calls = [c for c in calls if c[0] in (9, 10)]
    assert all(c[2] == ["--force"] for c in essentials_calls)


def test_essentials_flag_runs_after_assemble(tmp_path, monkeypatch):
    _write_two_lecture_course(tmp_path, monkeypatch)
    calls = _stub_run_stage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--all", "--assemble", "--essentials"])

    rp.main()

    stage_order = [c[0] for c in calls]
    assert stage_order.index(7) < stage_order.index(9)
    assert stage_order.index(9) < stage_order.index(10)


def test_essentials_skipped_when_an_earlier_lecture_failed(tmp_path, monkeypatch):
    _write_two_lecture_course(tmp_path, monkeypatch)
    calls = _stub_run_stage(monkeypatch, fail_stages={0})
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--all", "--from", "0", "--to", "0", "--essentials"])

    with pytest.raises(SystemExit) as exc_info:
        rp.main()
    assert exc_info.value.code != 0
    assert all(c[0] not in (9, 10) for c in calls)


def test_stage10_not_run_when_a_lecture_fails_stage9(tmp_path, monkeypatch):
    calls = _stub_run_stage(monkeypatch, fail_stages={9})
    monkeypatch.setattr(rp, "get_all_lecture_ids", lambda root: ["lecture01"])
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--all", "--from", "7", "--to", "7", "--essentials"])

    with pytest.raises(SystemExit) as exc_info:
        rp.main()
    assert exc_info.value.code != 0
    assert (10, None, []) not in calls
