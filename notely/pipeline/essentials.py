"""Stages [9]/[10]: essentials distillation.

The study guide (stage 7) reproduces every slide plus the professor's
asides -- accurate, but too long to revise from. These two stages produce
a condensed "what you actually must know" artifact on top of it, in two
tiers:

- Stage 9 (process_lecture_essentials): one Claude call per lecture, over
  that lecture's finished notes (output/notes/<lecture_id>.md), distilling
  a short must-know sheet (output/essentials/<lecture_id>.md) -- verbatim
  definitions/formulas and exam flags kept, restated slide bullets
  dropped.
- Stage 10 (process_course_essentials): one Claude call over the whole
  assembled study guide plus every stage-9 sheet found, producing a single
  course-level output/essentials.md.

Stage 10's shape is modeled on notely.pipeline.assemble.generate_topic_index
(single call, truncate defensively, write a raw prompt/response debug
file, degrade to a clean failure instead of raising on LlmApiError) --
but unlike the topic index, which is a bonus on top of assembly's real
deliverable (the guide still assembles fine without it), essentials.md IS
stage 10's only deliverable, so a failed call fails the whole stage rather
than silently "succeeding" with nothing written.

Both stages write outside output/notes/ on purpose:
notely.pipeline.assemble.find_lecture_notes globs *.md in output/notes/,
so an artifact written there would get swept straight into the study
guide -- see tests/test_essentials.py's regression guard.

Like stage 8 (PDF export), stages 9 and 10 are standalone: invoked
directly (scripts/09_lecture_essentials.py, scripts/10_course_essentials.py,
or run_pipeline.py's --essentials flag), never through the orchestrated
0-MAX_PIPELINE_STAGE sweep -- see notely/stages.py.
"""

import os
import sys

from ..io import save_json, write_text_atomic
from ..paths import PROJECT_ROOT
from ..ports import LlmApiError

INPUT_NOTES_DIR = PROJECT_ROOT / "output" / "notes"
OUTPUT_ESSENTIALS_DIR = PROJECT_ROOT / "output" / "essentials"
STUDY_GUIDE_PATH = PROJECT_ROOT / "output" / "study_guide.md"
COURSE_ESSENTIALS_PATH = PROJECT_ROOT / "output" / "essentials.md"
COURSE_ESSENTIALS_RAW_PATH = PROJECT_ROOT / "output" / "essentials_raw.json"

# A distillation task over already-generated notes, not the original
# transcript+slide synthesis stage 6 does -- same reasoning as
# NOTES_EXAMPLES_MODEL defaulting to Haiku rather than NOTES_MODEL.
DEFAULT_MODEL = "claude-haiku-4-5"
# Sheets are short by design; this is a safety ceiling only, not a target.
MAX_TOKENS = 4096
COURSE_MAX_TOKENS = 8192

# Claude's context window comfortably fits a full course guide (see
# assemble.generate_topic_index's own note on this project's ~110K-token
# guide), but cap defensively -- same value, same reasoning.
MAX_GUIDE_CHARS = 350_000

# Local names kept for the same atomic-write guarantee as every other
# stage -- see notely.io.
_write_json_atomic = save_json
_write_text_atomic = write_text_atomic


def load_dotenv_if_available() -> None:
    """Best-effort .env loading; never fatal if python-dotenv isn't installed."""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass


LECTURE_SYSTEM_PROMPT = r"""You are distilling a short "must-know" sheet from the complete study notes for one university lecture.

You are given the full per-slide notes for one lecture (slide text, professor's spoken commentary, everything already condensed once by an earlier pass).

Your job: cut it down further to only what a student absolutely must know for the exam -- the load-bearing content, not everything that was merely covered.

Rules:
- Keep every definition and formula exactly as given -- never paraphrase or approximate one loosely. Reproduce LaTeX delimiters (`$...$`, `$$...$$`) exactly as they appear in the source notes.
- Keep anything flagged as exam-relevant (e.g. "this will be on the exam," a professor's explicit emphasis or warning).
- Drop restated slide bullets, background context, and anything that was only mentioned in passing without being built on later.
- Do not invent, generalize, or add anything that is not already present in the notes you were given.
- Output structured markdown: a `##` heading naming the lecture's main topic (not "Lecture N"), then a concise bullet list of the must-know points, grouped by subtopic if that helps.
- Be aggressive about cutting length -- if the full notes are long, this sheet should be a fraction of their size. Only content, not commentary about the process of shortening it.
- LANGUAGE: write entirely in the same language as the source notes. Never translate it, never mix languages (LaTeX and standard abbreviations are the only exception)."""


COURSE_SYSTEM_PROMPT = r"""You are distilling a short "must-know" summary for an entire university course, from its assembled study guide (and, when given, per-lecture must-know sheets already distilled from it).

Your job: produce the smallest set of theory and practice a student needs to review to be ready for the exam, across the whole course -- not a second copy of the guide, and not a per-lecture recap (the per-lecture sheets, when given, already did that; use them as a shortlist of candidates, not something to re-summarize again).

Rules:
- Keep every definition and formula exactly as given -- never paraphrase or approximate one loosely. Reproduce LaTeX delimiters exactly.
- Prioritize anything the professor flagged as important, anything that recurs across multiple lectures, and foundational material later lectures build on.
- Only reference material that is actually present in what you were given. Never invent a concept, a lecture, or content that isn't there.
- Output structured markdown: a `# Essentials` heading (translate the word "Essentials" if the course's own language is not English -- keep the heading level), then organize by topic (not necessarily by lecture) with concise bullets.
- Be aggressive about cutting length -- this is meant to be read in well under an hour for an entire course, not per-lecture.
- LANGUAGE: write entirely in the same language as the guide's own content. Never translate it, never mix languages (LaTeX and standard abbreviations are the only exception)."""


def build_lecture_user_prompt(lecture_id: str, notes_text: str) -> str:
    return f"Lecture: {lecture_id}\n\nFull per-slide study notes for this lecture:\n\n{notes_text}"


def process_lecture_essentials(
    llm_client, lecture_id: str, index: int, total: int, force: bool = False
) -> bool:
    """Distill one lecture's notes into a must-know sheet. Returns False
    when there's genuinely nothing to do (no notes found) or the API call
    failed -- unlike stage 6's per-slide calls, this is a single call for
    the whole lecture, so a failure here means the lecture gets no sheet
    at all, not a degraded one."""
    input_path = INPUT_NOTES_DIR / f"{lecture_id}.md"
    output_path = OUTPUT_ESSENTIALS_DIR / f"{lecture_id}.md"
    raw_path = OUTPUT_ESSENTIALS_DIR / f"{lecture_id}_raw.json"

    if not input_path.exists():
        print(f"[error] {lecture_id}: no notes found at {input_path} (run stage 6 first)", file=sys.stderr)
        return False

    if output_path.exists() and not force:
        print(f"[skip] {lecture_id}: essentials already exist at {output_path} (use --force to redo)")
        return True

    try:
        notes_text = input_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[error] {lecture_id}: failed to read {input_path}: {e}", file=sys.stderr)
        return False

    model = os.environ.get("ESSENTIALS_MODEL", DEFAULT_MODEL)
    user_prompt = build_lecture_user_prompt(lecture_id, notes_text)

    request_payload = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": LECTURE_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    try:
        response = llm_client.create_message(**request_payload)
    except LlmApiError as e:
        print(f"  [{index}/{total}] lecture {lecture_id}: ERROR: {e}", file=sys.stderr)
        _write_json_atomic(
            raw_path,
            {"prompt": request_payload, "response": None, "model": model, "usage": None, "error": str(e)},
        )
        return False

    text = response.text
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    _write_json_atomic(
        raw_path,
        {"prompt": request_payload, "response": text, "model": model, "usage": usage},
    )
    _write_text_atomic(output_path, text)

    print(
        f"  [{index}/{total}] lecture {lecture_id}: ok "
        f"(input={usage['input_tokens']} output={usage['output_tokens']} tokens)"
    )
    return True


def _load_lecture_essentials_sheets(essentials_dir) -> list[tuple[str, str]]:
    """Return [(lecture_id, text), ...] for every stage-9 sheet found,
    sorted by lecture id -- same glob-and-sort-by-filename convention as
    assemble.find_lecture_notes. Missing directory (no one ran stage 9) is
    not an error -- stage 10 must still work off the guide alone."""
    if not essentials_dir.exists():
        return []
    return [(p.stem, p.read_text(encoding="utf-8")) for p in sorted(essentials_dir.glob("*.md"))]


def build_course_user_prompt(guide_text: str, sheets: list[tuple[str, str]]) -> str:
    parts = [f"Full assembled study guide:\n\n{guide_text}"]
    if sheets:
        parts.append("\n\nPer-lecture must-know sheets already distilled from the guide above:")
        for lecture_id, text in sheets:
            parts.append(f"\n--- {lecture_id} ---\n{text}")
    return "\n".join(parts)


def generate_course_essentials(llm_client, user_prompt: str, model: str, raw_debug_path) -> str | None:
    """The single LLM call for stage 10 -- same never-raises shape as
    assemble.generate_topic_index. Returns None on LlmApiError; the caller
    (process_course_essentials) decides what that means for the stage's
    own return value."""
    request_payload = {
        "model": model,
        "max_tokens": COURSE_MAX_TOKENS,
        "system": COURSE_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    try:
        response = llm_client.create_message(**request_payload)
    except LlmApiError as e:
        print(f"WARNING: course essentials failed: {e}", file=sys.stderr)
        _write_json_atomic(
            raw_debug_path,
            {"prompt": request_payload, "response": None, "model": model, "usage": None, "error": str(e)},
        )
        return None

    text = response.text
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    _write_json_atomic(
        raw_debug_path,
        {"prompt": request_payload, "response": text, "model": model, "usage": usage},
    )
    print(
        f"[done] course essentials generated (input={usage['input_tokens']} output={usage['output_tokens']} tokens)"
    )
    return text or None


def process_course_essentials(llm_client, force: bool = False) -> bool:
    """Distill the whole course's essentials.md from the assembled study
    guide plus every stage-9 sheet found. Returns False only on a real
    failure (no guide to read, or the LLM call failed) -- an
    already-generated essentials.md (not forced) is a successful no-op,
    matching every other stage's convention for the same situation."""
    if COURSE_ESSENTIALS_PATH.exists() and not force:
        print(f"[skip] Output already exists: {COURSE_ESSENTIALS_PATH} (use --force to redo)")
        return True

    if not STUDY_GUIDE_PATH.exists():
        print(f"[error] no study guide found at {STUDY_GUIDE_PATH} (run stage 7 first)", file=sys.stderr)
        return False

    try:
        guide_text = STUDY_GUIDE_PATH.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[error] failed to read {STUDY_GUIDE_PATH}: {e}", file=sys.stderr)
        return False

    guide_excerpt = guide_text[:MAX_GUIDE_CHARS]
    sheets = _load_lecture_essentials_sheets(OUTPUT_ESSENTIALS_DIR)
    user_prompt = build_course_user_prompt(guide_excerpt, sheets)

    model = os.environ.get("ESSENTIALS_MODEL", DEFAULT_MODEL)
    text = generate_course_essentials(llm_client, user_prompt, model, COURSE_ESSENTIALS_RAW_PATH)
    if text is None:
        return False

    _write_text_atomic(COURSE_ESSENTIALS_PATH, text)
    print(f"Course essentials written to {COURSE_ESSENTIALS_PATH}")
    return True
