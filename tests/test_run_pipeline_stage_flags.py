"""D7 (cleanup plan, Phase 2): run_pipeline.py built one `extra_args` list
and forwarded it, unchanged, to every stage script it ran (scripts/run_pipeline.py's
run_stage() calls at the bottom of main()). `--force` is fine that way --
every stage script accepts it -- but `--min-dwell` is stage 5
(segment_transcript)-specific: every other stage's argparse rejects an
unrecognized flag and exits 2, so `run_pipeline.py lecture01 --min-dwell 3.0`
crashed on stage 0 before stage 5 (the stage the flag was actually meant
for) ever ran, and `--all --assemble --min-dwell 3.0` crashed stage 7 for
the same reason.

Phase 6 fixed this structurally by routing every stage's argv through
notely.runner.build_tasks (the same logic webui/jobs.py's scheduler uses),
which also gave the CLI stage 3/4's own tuning flags (--crop, --ocr-lang,
--margin, etc.) it never had before -- previously only reachable from the
web UI.

run_stage() itself is monkeypatched to a recording stub in every test here,
so nothing actually spawns a subprocess or touches argparse -- these tests
pin routing (which stage numbers receive which flags), not each stage
script's own argument parsing (already covered elsewhere)."""

import sys

import pytest

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
    # order comes from notely.runner.build_tasks (Phase 6): per-stage flags
    # first, --force appended last -- argparse doesn't care about order, so
    # this pins presence/routing, not the exact sequence.
    assert by_stage[5] == ["--min-dwell", "2.5", "--force"]
    assert by_stage[0] == ["--force"]


# --- Phase 6: stage 3/4's own tuning flags, previously only reachable from
# the web UI --------------------------------------------------------------


def test_crop_and_threshold_reach_only_stage3(monkeypatch):
    calls = _stub_run_stage(monkeypatch)
    monkeypatch.setattr(
        sys, "argv", ["run_pipeline.py", "lecture01", "--crop", "0,0,1,1", "--threshold", "0.05"]
    )

    rp.main()

    by_stage = {stage_num: extra_args or [] for stage_num, _, extra_args in calls}
    assert by_stage[3] == ["--crop", "0,0,1,1", "--threshold", "0.05"]
    for stage_num, extra_args in by_stage.items():
        if stage_num != 3:
            assert "--crop" not in extra_args and "--threshold" not in extra_args


def test_ocr_lang_and_margin_reach_only_stage4(monkeypatch):
    calls = _stub_run_stage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "lecture01", "--ocr-lang", "eng", "--margin", "0.3"])

    rp.main()

    by_stage = {stage_num: extra_args or [] for stage_num, _, extra_args in calls}
    assert by_stage[4] == ["--ocr-lang", "eng", "--margin", "0.3"]
    for stage_num, extra_args in by_stage.items():
        if stage_num != 4:
            assert "--ocr-lang" not in extra_args and "--margin" not in extra_args


def test_unset_per_stage_flags_add_nothing(monkeypatch):
    """No --crop/--ocr-lang/etc given -> each stage's own argv gets no
    extra flags for them (defers to that stage script's own default)."""
    calls = _stub_run_stage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "lecture01"])

    rp.main()

    for _, _, extra_args in calls:
        assert extra_args == []


# --- Phase 6: assembly is now always forced, like the web UI already did --


def test_assembly_is_forced_even_without_the_force_flag(monkeypatch):
    """Assembly aggregates every lecture's notes into one file, so unlike
    stages 0-6 it can't safely skip based on existence -- doing so would
    silently produce a stale guide missing lectures processed since the
    last assembly. Previously the CLI only forced stage 7 when --force was
    passed (which also force-reran every other stage); the web UI always
    forced it. Phase 6 unifies on the web UI's (correct) behavior."""
    calls = _stub_run_stage(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "lecture01", "--assemble"])

    rp.main()

    stage7_calls = [(lec, extra_args or []) for stage_num, lec, extra_args in calls if stage_num == 7]
    assert stage7_calls == [(None, ["--force"])]


# --- Phase 6: a failed lecture skips its own remaining stages, not others -


def test_failed_lecture_skips_its_remaining_stages_but_not_other_lectures(monkeypatch):
    calls = _stub_run_stage(monkeypatch)

    def fake_run_stage(stage_num, lecture_id=None, extra_args=None):
        calls.append((stage_num, lecture_id, extra_args))
        return not (lecture_id == "lecture01" and stage_num == 3)

    monkeypatch.setattr(rp, "run_stage", fake_run_stage)
    monkeypatch.setattr(rp, "get_all_lecture_ids", lambda project_root: ["lecture01", "lecture02"])
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--all", "--from", "3", "--to", "5"])

    with pytest.raises(SystemExit) as exc_info:
        rp.main()
    assert exc_info.value.code != 0

    stages_seen = {lec: [s for s, lc, _ in calls if lc == lec] for lec in ("lecture01", "lecture02")}
    assert stages_seen["lecture01"] == [3]  # stopped after its own failure
    assert stages_seen["lecture02"] == [3, 4, 5]  # unaffected by lecture01's failure
