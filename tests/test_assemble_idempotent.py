"""D6 (cleanup plan, Phase 2): `run_pipeline.py --all --assemble` failed on
a second run of an otherwise-unchanged course. Stages 5 and 6 already treat
"output already exists, not forced" as a successful no-op (return True --
see scripts/05_segment_transcript.py's segment_transcript and
scripts/06_generate_notes.py's process_lecture), but stage 7's
assemble_guide treated the exact same situation as a failure (return
False), so main() called sys.exit(1) and the whole pipeline run reported
failure even though every stage had already correctly completed."""

import sys

import pytest

from conftest import load_stage
from notely.pipeline import assemble as m07

s07 = load_stage("07_assemble.py")


@pytest.fixture()
def assembled_project(tmp_path, monkeypatch):
    """A project root where stage 7 has already run once successfully.
    Patches notely.pipeline.assemble (Phase 5 moved assemble_guide there)
    -- s07.get_project_root/assemble_guide are the same objects, but their
    own global lookups resolve against that module, not s07's re-export."""
    monkeypatch.setattr(m07, "get_project_root", lambda: tmp_path)
    notes_dir = tmp_path / "output" / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "lecture01.md").write_text("# lecture01\n\nSome notes.\n")
    assert m07.assemble_guide(force=True, topic_index=False) is True
    assert (tmp_path / "output" / "study_guide.md").exists()
    return tmp_path


def test_assemble_guide_returns_true_on_an_already_assembled_project(assembled_project):
    assert m07.assemble_guide(force=False, topic_index=False) is True


def test_stage07_main_does_not_exit_nonzero_on_a_second_unforced_run(assembled_project, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["07_assemble.py"])
    # must not raise SystemExit at all -- rerunning an unchanged pipeline is
    # a success, not a failure
    s07.main()
