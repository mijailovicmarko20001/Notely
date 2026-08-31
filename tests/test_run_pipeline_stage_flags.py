"""D7 (cleanup plan, Phase 2): run_pipeline.py built one `extra_args` list
and forwarded it, unchanged, to every stage script it ran (scripts/run_pipeline.py's
run_stage() calls at the bottom of main()). `--force` is fine that way --
every stage script accepts it -- but `--min-dwell` is stage 5
(segment_transcript)-specific: every other stage's argparse rejects an
unrecognized flag and exits 2, so `run_pipeline.py lecture01 --min-dwell 3.0`
crashed on stage 0 before stage 5 (the stage the flag was actually meant
for) ever ran, and `--all --assemble --min-dwell 3.0` crashed stage 7 for
the same reason.

run_stage() itself is monkeypatched to a recording stub in every test here,
so nothing actually spawns a subprocess or touches argparse -- these tests
pin routing (which stage numbers receive which flags), not each stage
script's own argument parsing (already covered elsewhere)."""

import sys

from conftest import load_stage

rp = load_stage("run_pipeline.py")


def _stub_run_stage(monkeypatch):
    calls = []
    monkeypatch.setattr(
        rp,
        "run_stage",
        lambda stage_num, lecture_id=None, extra_args=None: (
            calls.append((stage_num, lecture_id, extra_args)) or True
        ),
    )
    return calls


def test_min_dwell_reaches_only_stage5(monkeypatch):
    calls = _stub_run_stage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "lecture01", "--min-dwell", "3.0"])

    rp.main()

    by_stage = {stage_num: extra_args or [] for stage_num, _, extra_args in calls}
    assert by_stage[5] == ["--min-dwell", "3.0"]
    for stage_num, extra_args in by_stage.items():
        if stage_num != 5:
            assert "--min-dwell" not in extra_args, f"stage {stage_num} received --min-dwell: {extra_args}"


def test_force_flag_still_reaches_every_stage(monkeypatch):
    calls = _stub_run_stage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "lecture01", "--force"])

    rp.main()

    assert calls  # sanity: the loop actually ran
    for _, _, extra_args in calls:
        assert "--force" in (extra_args or [])


def test_stage7_assembly_call_does_not_receive_min_dwell(monkeypatch):
    calls = _stub_run_stage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "lecture01", "--assemble", "--min-dwell", "3.0"])

    rp.main()

    stage7_calls = [extra_args or [] for stage_num, _, extra_args in calls if stage_num == 7]
    assert stage7_calls, "stage 7 should have been invoked"
    assert "--min-dwell" not in stage7_calls[0]


def test_both_force_and_min_dwell_route_correctly_together(monkeypatch):
    calls = _stub_run_stage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "lecture01", "--force", "--min-dwell", "2.5"])

    rp.main()

    by_stage = {stage_num: extra_args or [] for stage_num, _, extra_args in calls}
    assert by_stage[5] == ["--force", "--min-dwell", "2.5"]
    assert by_stage[0] == ["--force"]
