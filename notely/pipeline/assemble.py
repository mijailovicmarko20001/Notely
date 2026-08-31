"""Stage [7]: assembly -- moved here from scripts/07_assemble.py (Phase 5),
which is now a thin CLI wrapper around assemble_guide().

Concatenates per-lecture note files into a single study guide with a
table of contents, in sorted lecture order. Optional cross-lecture LLM
pass: assemble_guide(topic_index=True) (off by default -- costs a real
API call over the whole assembled guide; see generate_topic_index).
"""

import os
import sys
from pathlib import Path

from ..io import save_json, write_text_atomic
from ..paths import PROJECT_ROOT
from ..ports import LlmApiError


def get_project_root():
    """Return the project root directory (parent of scripts/)."""
    return PROJECT_ROOT


def load_dotenv_if_available() -> None:
    """Best-effort .env loading (same pattern as stages 00/01/04/06); never
    fatal if python-dotenv isn't installed. Only actually needed by
    --topic-index, but harmless to always attempt."""
    try:
        from dotenv import load_dotenv

        load_dotenv(get_project_root() / ".env")
    except ImportError:
        pass


def find_lecture_notes(project_root):
    """Find all lecture note files in output/notes/ and return sorted."""
    notes_dir = project_root / "output" / "notes"
    if not notes_dir.exists():
        return []

    # Find all .md files matching lecture pattern
    note_files = sorted(notes_dir.glob("*.md"))
    return note_files


def parse_lecture_id_from_filename(filename):
    """Extract lecture ID from filename (e.g., 'lecture01.md' -> 'lecture01')."""
    return filename.stem


def read_lecture_notes(path):
    """Read a lecture note file, fixing image paths for the guide's location.

    Notes live in output/notes/ and embed images as ../slides_extracted/...
    (the clean deck render) and, when NOTES_SEND_FRAME_IMAGE was on,
    ../frame_events/... too (the actual on-screen capture, showing any live
    annotations). The assembled guide lives one level up in output/, where
    both are one directory shallower.
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    text = text.replace("](../slides_extracted/", "](slides_extracted/")
    text = text.replace("](../frame_events/", "](frame_events/")
    return text


# Local name kept (rather than updating every call site below) for the
# same atomic-write guarantee, now backed by one shared implementation --
# see notely.io.
_write_json_atomic = save_json


def build_table_of_contents(lectures):
    """Build a markdown table of contents from lecture list."""
    lines = ["# Table of Contents\n"]
    for lecture_id in lectures:
        lines.append(f"- [{lecture_id}](#{lecture_id})")
    lines.append("")
    return "\n".join(lines)


TOPIC_INDEX_SYSTEM_PROMPT = r"""You are building a cross-lecture topic index for an assembled university-course study guide made of per-lecture notes concatenated together.

You will be given the full assembled guide (lecture headings look like "# lectureNN" and are valid markdown link anchors, e.g. link to one as [text](#lectureNN)).

Your job: surface connections and exam-relevant material that spans MULTIPLE lectures -- the kind of thing a student reviewing one lecture at a time would miss. Do not summarize each lecture individually; the per-lecture notes already do that.

Output structured markdown:
- A `# Topic Index` heading.
- A handful of cross-lecture topic groupings (e.g. "X, introduced in [lectureNN](#lectureNN), is used again in [lectureMM](#lectureMM) for..."), each linking to every lecture it touches via the #lectureNN anchors.
- A final "Recurring exam-relevant emphases" section collecting anything the professor flagged as important in more than one lecture, if any.

Rules:
- Only reference material that is actually present in the text you were given. Never invent a connection, a lecture, or content that isn't there.
- LANGUAGE: write entirely in the same language as the guide's own content. Never translate it, never mix languages (the literal heading "# Topic Index" and lecture anchors are the only exceptions -- translate the heading text itself if the guide isn't in English, keep the `# Topic Index` structure).
- Be concise -- this is a map of connections, not a second study guide."""


def generate_topic_index(llm_client, full_guide_text: str, model: str, raw_debug_path: Path) -> str | None:
    """Optional second LLM pass (per CLAUDE.md's [7] Assembly section): a
    cross-lecture topic index surfacing connections/exam-relevant material
    that spans multiple lectures, which per-lecture notes structurally
    can't capture on their own. Returns markdown to insert into the guide,
    or None on failure -- assembly should still succeed with just the
    per-lecture notes if this one extra call fails.

    NOT wired into the web UI or any automatic pipeline run -- it's a real
    API call over the whole assembled guide (potentially hundreds of
    thousands of tokens for a full course), so it stays an explicit
    `--topic-index` CLI opt-in rather than a silent cost added to every
    run, matching this project's stance on cloud calls elsewhere."""
    # Claude's context window comfortably fits a full course guide (this
    # project's own 22-lecture guide is ~440K chars / ~110K tokens), but
    # cap defensively so a much larger course doesn't blow past it
    # alongside the system prompt + output budget.
    MAX_GUIDE_CHARS = 350_000
    guide_excerpt = full_guide_text[:MAX_GUIDE_CHARS]

    request_payload = {
        "model": model,
        "max_tokens": 8192,
        "system": TOPIC_INDEX_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": guide_excerpt}],
    }

    try:
        response = llm_client.create_message(**request_payload)
    except LlmApiError as e:
        print(f"WARNING: cross-lecture topic index failed: {e}", file=sys.stderr)
        _write_json_atomic(
            raw_debug_path,
            {"prompt": request_payload, "response": None, "model": model, "error": str(e)},
        )
        return None

    text = response.text
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    _write_json_atomic(
        raw_debug_path,
        {"prompt": request_payload, "response": text, "model": model, "usage": usage},
    )
    print(
        f"cross-lecture topic index generated (input={usage['input_tokens']} output={usage['output_tokens']} tokens)"
    )
    return text or None


def assemble_guide(force=False, topic_index=False, llm_client=None):
    """Main assembly logic. Returns False only on a real failure (no notes
    found, or a note file that couldn't be read) -- an already-assembled
    guide (not forced) is a successful no-op, matching stages 5 and 6's
    convention for the same situation, so `run_pipeline.py --all --assemble`
    stays idempotent on an unchanged course.

    llm_client: an LlmClient (see notely.ports), only used when
    topic_index=True. Defaults to the real Anthropic-backed adapter;
    tests inject a fake instead of needing a real API key."""
    project_root = get_project_root()

    output_path = project_root / "output" / "study_guide.md"

    # Check if output exists and --force not set
    if output_path.exists() and not force:
        print(f"[skip] Output already exists: {output_path} (use --force to redo)")
        return True

    # Find all lecture notes
    note_files = find_lecture_notes(project_root)
    if not note_files:
        print("Warning: no lecture notes found in output/notes/", file=sys.stderr)
        return False

    # Build lecture list and load content
    lectures = []
    content_parts = []

    for note_file in note_files:
        lecture_id = parse_lecture_id_from_filename(note_file)
        lectures.append(lecture_id)

        try:
            note_content = read_lecture_notes(note_file)
            # Note files begin with their own "# lectureNN" H1 — only add a
            # header if one is missing (a duplicate H1 makes PDF export emit
            # a near-blank page per lecture via page-break-before).
            if not note_content.lstrip().startswith(f"# {lecture_id}"):
                content_parts.append(f"# {lecture_id}\n")
            content_parts.append(note_content)
            content_parts.append("")  # Blank line between lectures
        except Exception as e:
            print(f"Error reading {note_file}: {e}", file=sys.stderr)
            return False

    # Build final guide with TOC
    toc = build_table_of_contents(lectures)
    full_guide = toc + "\n".join(content_parts)

    if topic_index:
        if llm_client is not None:
            # caller (a test, typically) already supplied one -- skip the
            # API-key check entirely, it's only a guard against constructing
            # a real client with no credentials.
            client_for_index = llm_client
        else:
            load_dotenv_if_available()
            if not os.environ.get("ANTHROPIC_API_KEY"):
                print(
                    "WARNING: --topic-index requested but ANTHROPIC_API_KEY is not set; skipping",
                    file=sys.stderr,
                )
                client_for_index = None
            else:
                from ..adapters.anthropic_llm import AnthropicLlmClient

                client_for_index = AnthropicLlmClient(max_retries=5)

        if client_for_index is not None:
            model = os.environ.get("NOTES_MODEL", "claude-sonnet-5")
            index_md = generate_topic_index(
                client_for_index, full_guide, model, project_root / "output" / "topic_index_raw.json"
            )
            if index_md:
                full_guide = toc + index_md + "\n\n---\n\n" + "\n".join(content_parts)

    # Atomic write (see notely.io): a killed process can never leave a
    # truncated-but-non-empty study_guide.md that a later run's
    # exists()-and-nonempty skip check would wrongly trust as done.
    write_text_atomic(output_path, full_guide)

    print(f"Study guide assembled into {output_path}")
    print(f"Included {len(lectures)} lectures: {', '.join(lectures)}")

    return True
