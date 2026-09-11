"""Contract test for notely.stages, written before the implementation
(Phase 4, "new structure").

One Stage record per pipeline stage (0-11) is the single source for stage
names, script filenames, and per-lecture output artifact paths --
collapsing ~25 previously hardcoded copies of this same information
across webui/progress.py, webui/jobs.py, webui/media.py, webui/models.py,
webui/routes/jobs.py, and run_pipeline.py. Frontend consumption
(index.html/app.js) is deferred to Phase 7. Stages 9-11 (essentials
distillation, practice exams) followed the same registry-first pattern
established here, staying outside MAX_PIPELINE_STAGE like stage 8 -- see
notely/stages.py."""

from pathlib import Path

from notely.stages import MAX_PIPELINE_STAGE, PER_LECTURE_STAGES, STAGES, STAGES_BY_NUMBER

INPUT_DIR = Path("/tmp/notely-test-input")
OUTPUT_DIR = Path("/tmp/notely-test-output")


def test_stages_cover_0_through_11_in_order():
    assert [s.number for s in STAGES] == list(range(12))


def test_stages_by_number_matches_stages():
    assert STAGES_BY_NUMBER[4].name == "Match frames to slides"
    assert STAGES_BY_NUMBER[4] is STAGES[4]


def test_every_stage_has_a_name_and_script():
    for stage in STAGES:
        assert stage.name
        assert stage.script.endswith(".py")
        assert stage.script.startswith(f"{stage.number:02d}_")


def test_per_lecture_stages_are_0_through_6():
    assert PER_LECTURE_STAGES == list(range(7))


def test_stage7_and_8_are_not_per_lecture():
    assert STAGES_BY_NUMBER[7].per_lecture is False
    assert STAGES_BY_NUMBER[8].per_lecture is False


def test_stage9_and_10_are_not_per_lecture():
    # Stage 9 (per-lecture essentials sheet) does take a lecture_id, same as
    # stage 8 (PDF export) -- but like stage 8, it's a standalone step
    # invoked outside the orchestrated 0-7 sweep (run_pipeline.py's
    # --essentials flag calls it directly, not via build_tasks), so it's
    # deliberately excluded from PER_LECTURE_STAGES rather than joining it.
    assert STAGES_BY_NUMBER[9].per_lecture is False
    assert STAGES_BY_NUMBER[10].per_lecture is False


def test_max_pipeline_stage_is_7():
    # The highest stage run_pipeline.py / the job scheduler orchestrates.
    # Stage 8 (PDF export) is invoked separately, not part of that range.
    assert MAX_PIPELINE_STAGE == 7


def test_per_lecture_artifact_paths_use_the_lecture_id():
    stage4 = STAGES_BY_NUMBER[4]
    path = stage4.artifact_path(INPUT_DIR, OUTPUT_DIR, "lecture03")
    assert path.name == "lecture03.json"
    assert path.parent.name == "slide_timelines"
    assert path.parent.parent == OUTPUT_DIR


def test_stage0_artifact_path_is_under_input_dir():
    stage0 = STAGES_BY_NUMBER[0]
    path = stage0.artifact_path(INPUT_DIR, OUTPUT_DIR, "lecture01")
    assert path == INPUT_DIR / "videos" / "lecture01.mp4"


def test_course_level_artifact_path_ignores_lecture_id():
    stage7 = STAGES_BY_NUMBER[7]
    assert stage7.artifact_path(INPUT_DIR, OUTPUT_DIR, None).name == "study_guide.md"


def test_stage8_has_no_tracked_artifact():
    assert STAGES_BY_NUMBER[8].artifact_path is None


def test_stage9_artifact_path_uses_the_lecture_id():
    stage9 = STAGES_BY_NUMBER[9]
    path = stage9.artifact_path(INPUT_DIR, OUTPUT_DIR, "lecture03")
    assert path == OUTPUT_DIR / "essentials" / "lecture03.md"


def test_stage10_artifact_path_ignores_lecture_id():
    stage10 = STAGES_BY_NUMBER[10]
    path = stage10.artifact_path(INPUT_DIR, OUTPUT_DIR, None)
    assert path == OUTPUT_DIR / "essentials.md"


def test_stage11_is_not_per_lecture():
    # Practice exams are course-level, like stage 7 -- no lecture_id at all.
    assert STAGES_BY_NUMBER[11].per_lecture is False


def test_stage11_artifact_path_is_the_first_exam_paper():
    stage11 = STAGES_BY_NUMBER[11]
    path = stage11.artifact_path(INPUT_DIR, OUTPUT_DIR, None)
    assert path == OUTPUT_DIR / "exams" / "exam_01.md"
