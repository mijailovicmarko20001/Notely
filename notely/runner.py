"""Orchestration: translate a course-wide pipeline request (which lectures,
which stages, which per-stage option overrides) into a flat list of
per-stage subprocess argv lists.

Moved here from webui/jobs.py (Phase 6 of the cleanup plan) so both
webui/jobs.py's scheduler and scripts/run_pipeline.py drive the exact same
logic -- previously run_pipeline.py reimplemented a weaker version that
could only forward --force/--min-dwell (D7), with no way to pass stage 3's
--crop/--threshold or stage 4's --ocr-lang/--margin/etc. at all. Driven by
the Phase 4 stage registry (notely.stages) rather than a hardcoded "stage
!= 7" check.
"""

from .stages import MAX_PIPELINE_STAGE, PER_LECTURE_STAGES


def build_tasks(lecture_ids, stages, options, force, has_api_key):
    """Translate a pipeline request into per-stage argv lists.

    Returns [(lecture_id, stage, extra_argv), ...]: one tuple per
    lecture/stage combination, in lecture order, plus a single trailing
    (None, MAX_PIPELINE_STAGE, ["--force"]) if that stage was requested --
    it aggregates every lecture's notes into one file, so unlike stages 0-6
    (safe to skip if that lecture's own artifact already exists) it must
    always re-run to pick up lectures processed since the last assembly;
    skipping it based on existence would silently produce a stale guide."""
    opts = options or {}

    def flag(name, key):
        v = opts.get(key)
        return [name, str(v)] if v not in (None, "") else []

    tasks = []
    per_lecture_stages = [s for s in stages if s in PER_LECTURE_STAGES]
    for lec in lecture_ids:
        for s in per_lecture_stages:
            if s == 6 and not has_api_key:
                continue  # note generation locked without a key
            extra = []
            if s == 3:
                extra += (
                    flag("--crop", "crop") + flag("--threshold", "threshold") + flag("--interval", "interval")
                )
            elif s == 4:
                extra += (
                    flag("--ocr-lang", "ocr_lang")
                    + flag("--margin", "margin")
                    + flag("--stay-margin", "stay_margin")
                    + flag("--confidence-threshold", "confidence_threshold")
                    + flag("--min-forward-score", "min_forward_score")
                    + flag("--example-score-max", "example_score_max")
                    + flag("--example-ink-delta", "example_ink_delta")
                    + flag("--example-ink-text-overlap-min", "example_ink_text_overlap_min")
                    + flag("--example-ink-novel-word-min", "example_ink_novel_word_min")
                )
            elif s == 5:
                extra += flag("--min-dwell", "min_dwell")
            if force:
                extra.append("--force")
            tasks.append((lec, s, extra))
    if MAX_PIPELINE_STAGE in stages:
        tasks.append((None, MAX_PIPELINE_STAGE, ["--force"]))
    return tasks
