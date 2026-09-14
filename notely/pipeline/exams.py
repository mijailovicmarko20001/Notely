"""Stage [11]: practice exam generation.

Two phases:

1. Format analysis (generate_exam_format / load_format_blueprint): one
   Claude call over uploaded past-exam pages (input/exams/*.pdf, optional
   -- see collect_uploaded_exam_pages) plus an excerpt of the course
   material, producing a JSON "blueprint" -- section structure, question
   types, counts, point distribution, duration, phrasing/instruction
   language. The uploaded exams are a FORMAT template ONLY: the blueprint
   schema has no field for actual question text, and FORMAT_SYSTEM_PROMPT
   explicitly forbids echoing any. Cached to output/exams/_format.json so
   regenerating papers with a tweaked prompt doesn't re-pay for analysis
   -- same convention as notes.py's NOTES_DETECT_EXAMPLES cache.

2. Paper generation (generate_one_exam / process_exam_generation): one
   call per requested paper. The blueprint plus the course material
   (output/essentials.md if present, else output/study_guide.md) go in;
   a single response containing both the student-facing paper and a
   separate answer key comes out, split on ANSWER_KEY_MARKER and written
   to output/exams/exam_NN.md / exam_NN_key.md. One call rather than two
   separate paper/key calls: cheaper, and keeps the key perfectly in sync
   with the paper it actually answers rather than risking two independent
   generations drifting apart.
"""

import json
import os
import re
import sys

from ..io import save_json, write_text_atomic
from ..paths import INPUT_DIR, OUTPUT_DIR, PROJECT_ROOT
from ..pipeline.examples import load_frame_image_b64
from ..ports import LlmApiError

INPUT_EXAMS_DIR = INPUT_DIR / "exams"
OUTPUT_EXAMS_DIR = OUTPUT_DIR / "exams"
EXAM_IMAGE_DIR = OUTPUT_EXAMS_DIR / "_source_images"
FORMAT_CACHE_PATH = OUTPUT_EXAMS_DIR / "_format.json"
STUDY_GUIDE_PATH = OUTPUT_DIR / "study_guide.md"
COURSE_ESSENTIALS_PATH = OUTPUT_DIR / "essentials.md"

# Practice-exam generation needs to reason about structure, difficulty,
# and faithfulness to the course material -- the strong model, not the
# Haiku-tier one essentials/example-confirmation use for lighter tasks.
DEFAULT_MODEL = "claude-sonnet-5"

# A page with less extracted text than this is treated as scanned (image,
# not real text layer) and rendered to PNG for a vision block instead.
SCANNED_TEXT_THRESHOLD = 50

FORMAT_MAX_TOKENS = 2048
EXAM_MAX_TOKENS = 8192
# Same reasoning/value as assemble.generate_topic_index's MAX_GUIDE_CHARS.
MAX_COURSE_MATERIAL_CHARS = 350_000

# Literal separator the exam-generation response is split on into
# (student paper, answer key). Chosen to be extremely unlikely to appear
# in real markdown content and easy to instruct the model to reproduce
# exactly.
ANSWER_KEY_MARKER = "\n<<<ANSWER_KEY>>>\n"

_write_json_atomic = save_json
_write_text_atomic = write_text_atomic


def load_dotenv_if_available() -> None:
    """Best-effort .env loading; never fatal if python-dotenv isn't installed."""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass


# --- extraction --------------------------------------------------------


def extract_exam_pages(
    pdf_path, image_dir, scanned_text_threshold: int = SCANNED_TEXT_THRESHOLD
) -> list[dict]:
    """Extract per-page text from an uploaded exam PDF via pypdfium2 (same
    library/approach as notely.pipeline.slides.extract_from_pdf). A page
    whose extracted text is shorter than scanned_text_threshold is assumed
    to be a scanned/image page and is rendered to PNG under image_dir
    instead -- only scanned pages are rendered, since most exam PDFs are
    real text and rendering every page would be wasted work.

    Returns [{"page_number": int, "text": str, "image_path": str|None}, ...].
    image_path, when set, is relative to PROJECT_ROOT (same convention as
    stage 3/4's frame_image_path) so load_frame_image_b64 can load it."""
    import pypdfium2 as pdfium

    pages = []
    with pdfium.PdfDocument(str(pdf_path)) as doc:
        for i, page in enumerate(doc, start=1):
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range().strip()
                finally:
                    textpage.close()

                image_path = None
                if len(text) < scanned_text_threshold:
                    image_dir.mkdir(parents=True, exist_ok=True)
                    scale = 150 / 72  # match slides.render_pdf_to_images' DPI convention
                    bitmap = page.render(scale=scale)
                    try:
                        img_path = image_dir / f"{pdf_path.stem}_page_{i:03d}.png"
                        bitmap.to_pil().save(str(img_path))
                        try:
                            image_path = str(img_path.relative_to(PROJECT_ROOT))
                        except ValueError:
                            # image_dir isn't under PROJECT_ROOT (e.g. a tmp
                            # dir in a test) -- fall back to an absolute
                            # path rather than raising.
                            image_path = str(img_path)
                    finally:
                        bitmap.close()
            finally:
                page.close()
            pages.append({"page_number": i, "text": text, "image_path": image_path})
    return pages


def collect_uploaded_exam_pages(input_dir, image_dir) -> list[dict]:
    """Extract every page of every uploaded exam PDF in input_dir, tagged
    with its source filename. Missing/empty directory -> [] -- uploaded
    exams are optional input, not required."""
    if not input_dir.exists():
        return []
    pages = []
    for pdf_path in sorted(input_dir.glob("*.pdf")):
        for page in extract_exam_pages(pdf_path, image_dir):
            pages.append({"source": pdf_path.name, **page})
    return pages


# --- JSON response parsing ------------------------------------------------


def _parse_json_object(text: str) -> dict | None:
    """Parse a model response as a JSON object, tolerating a fenced code
    block or prose wrapped around it (same tolerance as
    examples.parse_example_verdict, generalized beyond that function's
    fixed verdict schema). Returns None on anything that doesn't parse --
    the caller treats that as "format analysis failed," never raises."""
    if not text:
        return None

    candidate = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if brace_match:
            candidate = brace_match.group(0)

    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


# --- format analysis -----------------------------------------------------

FORMAT_SYSTEM_PROMPT = r"""You are analyzing the FORMAT of one or more past exams from a university course, to help generate NEW practice exams in the same style. You are given each uploaded exam's pages (as text, or as an image when a page is scanned) and an excerpt of the course's own study material.

Your job is to describe the exam's FORMAT ONLY -- never its specific content. Do not reproduce, quote, paraphrase, or summarize any actual question from the uploaded exams anywhere in your output. If no exams were uploaded, infer a sensible format from the course material's scope and topic alone (a reasonable number of questions and point distribution for a course of this size).

Respond with ONLY a JSON object, no other text, no markdown fences, no explanation, matching this shape:
{
  "total_questions": <int>,
  "sections": [
    {"name": "<section name>", "question_type": "multiple_choice|short_answer|proof|calculation|coding|essay|...", "count": <int>, "points_each": <number>}
  ],
  "total_points": <number or null>,
  "duration_minutes": <int or null>,
  "instructions_language": "<ISO 639-1 code, e.g. 'sr', 'en'>",
  "phrasing_notes": "<1-3 sentences describing tone/phrasing conventions -- e.g. how questions are typically worded -- with NO actual question content>",
  "difficulty_notes": "<1-2 sentences on difficulty level and depth expected>"
}

Rules:
- "phrasing_notes" and "difficulty_notes" describe HOW the exam is written, never WHAT it asks.
- If uploaded exams disagree in format, synthesize a reasonable common structure rather than just copying the first one.
- Every field must be present; use null only where explicitly allowed above."""


def build_format_analysis_content(pages: list[dict], course_material_excerpt: str) -> list[dict]:
    """Build the Claude content blocks for the format-analysis call: one
    text block with the course material excerpt plus every uploaded
    page's extracted text (labeled by source+page), followed by an image
    block for each scanned page (grouped at the end rather than
    interleaved -- scanned pages are the exception, not the common case,
    so this keeps the common path a single text block)."""
    text_parts = [f"Course material excerpt (for context on scope/level):\n\n{course_material_excerpt}"]
    if pages:
        text_parts.append("\n\nUploaded past exam pages:")
        for p in pages:
            label = f"--- {p.get('source', 'exam')} page {p['page_number']} ---"
            text_parts.append(f"\n{label}\n{p['text'] or '(scanned page, see attached image)'}")
    else:
        text_parts.append(
            "\n\n(No past exams were uploaded -- infer a sensible format from the course material.)"
        )

    content = [{"type": "text", "text": "\n".join(text_parts)}]
    for p in pages:
        if p["image_path"]:
            b64 = load_frame_image_b64(p["image_path"])
            if b64:
                content.append(
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}
                )
    return content


def generate_exam_format(
    llm_client, pages: list[dict], course_material_excerpt: str, model: str, raw_debug_path
) -> dict | None:
    """The single LLM call behind load_format_blueprint. Returns the
    parsed blueprint dict, or None on an LlmApiError or an unparseable
    response -- never raises."""
    content = build_format_analysis_content(pages, course_material_excerpt)
    request_payload = {
        "model": model,
        "max_tokens": FORMAT_MAX_TOKENS,
        "system": FORMAT_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": content}],
    }

    debug_content = [
        {
            **block,
            "source": {**block["source"], "data": f"<omitted, {len(block['source']['data'])} base64 chars>"},
        }
        if block.get("type") == "image"
        else block
        for block in content
    ]
    debug_prompt = {**request_payload, "messages": [{"role": "user", "content": debug_content}]}

    try:
        response = llm_client.create_message(**request_payload)
    except LlmApiError as e:
        print(f"WARNING: exam format analysis failed: {e}", file=sys.stderr)
        _write_json_atomic(
            raw_debug_path,
            {"prompt": debug_prompt, "response": None, "model": model, "usage": None, "error": str(e)},
        )
        return None

    text = response.text
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    blueprint = _parse_json_object(text)
    _write_json_atomic(
        raw_debug_path,
        {"prompt": debug_prompt, "response": text, "model": model, "usage": usage, "parsed": blueprint},
    )
    if blueprint is None:
        print("WARNING: exam format analysis returned an unparseable response", file=sys.stderr)
        return None

    print(f"exam format analyzed (input={usage['input_tokens']} output={usage['output_tokens']} tokens)")
    return blueprint


def load_format_blueprint(llm_client, course_material_excerpt: str, force: bool = False) -> dict | None:
    """Cache-aware wrapper around generate_exam_format: reuses
    output/exams/_format.json unless force, so regenerating papers with a
    tweaked prompt doesn't re-pay for format analysis. Returns None if no
    cached blueprint exists and analysis fails."""
    if FORMAT_CACHE_PATH.exists() and not force:
        cached = json.loads(FORMAT_CACHE_PATH.read_text(encoding="utf-8"))
        print(f"using cached exam format -> {FORMAT_CACHE_PATH}")
        return cached["blueprint"]

    pages = collect_uploaded_exam_pages(INPUT_EXAMS_DIR, EXAM_IMAGE_DIR)
    model = os.environ.get("EXAM_MODEL", DEFAULT_MODEL)
    blueprint = generate_exam_format(
        llm_client, pages, course_material_excerpt, model, OUTPUT_EXAMS_DIR / "_format_raw.json"
    )
    if blueprint is None:
        return None

    _write_json_atomic(FORMAT_CACHE_PATH, {"model": model, "blueprint": blueprint})
    return blueprint


# --- paper + key generation ------------------------------------------------

EXAM_SYSTEM_PROMPT = r"""You are generating a NEW practice exam for a university course, from its own course material, matching a given format blueprint.

You are given:
- a JSON "format blueprint" describing structure, question types, counts, points, duration, and phrasing conventions (derived from past exams, or inferred from the course if none existed) -- NEVER actual past questions, only format metadata.
- the course's own material (study guide and/or distilled essentials).

Your job: write ONE new exam matching the blueprint's structure and difficulty, with every question answerable strictly from the course material you were given. Never invent facts, formulas, or content not present in the course material. Never reproduce a question from any past exam -- you were not given any past exam content, only its format, so this should not be possible, but if the blueprint's phrasing_notes happen to echo a specific scenario, generalize it into a new one instead of reusing it verbatim.

Output EXACTLY two markdown documents, separated by this literal line on its own:
<<<ANSWER_KEY>>>

The FIRST document (the student-facing exam paper):
- A `# ` heading naming the course/exam.
- Duration and total points, if the blueprint specifies them.
- Each section from the blueprint as its own `## ` heading, with its questions numbered and point values shown.
- Questions ONLY -- no answers, no hints, no solutions anywhere in this document.

The SECOND document (the answer key, after the `<<<ANSWER_KEY>>>` line):
- A `# Answer Key` heading (translate if the course material isn't in English).
- Every question's full answer/solution, in the same order and numbering as the paper.
- For each answer, a brief note of which part of the course material it draws from, if identifiable (e.g. a lecture or topic name).
- For multi-step or proof/calculation questions, show the reasoning, not just a final answer.

Rules:
- LANGUAGE: write both documents entirely in the blueprint's instructions_language (or the course material's own language if unset). Never mix languages, except LaTeX and standard abbreviations.
- Formulas as proper LaTeX (`$...$` inline, `$$...$$` standalone), exactly like the source material -- never plain text or unicode math symbols.
- Do not add a heading, preamble, or any text before the exam paper's own `# ` heading, and do not add anything after the answer key."""


def build_exam_user_prompt(blueprint: dict, course_material: str, paper_number: int, count: int) -> str:
    variant_note = (
        f"\n\nThis is variant {paper_number} of {count} being generated for this course -- make its specific "
        f"questions and scenarios different from a typical single generation, while still matching the blueprint."
        if count > 1
        else ""
    )
    return (
        f"Format blueprint:\n{json.dumps(blueprint, indent=2)}\n\n"
        f"Course material:\n\n{course_material}"
        f"{variant_note}"
    )


def split_exam_and_key(text: str) -> tuple[str, str | None]:
    """Split a generation response into (paper, key) on ANSWER_KEY_MARKER,
    tolerating any amount of surrounding blank-line whitespace the model
    might put around the literal marker line (observed live: the model put
    it on its own paragraph, i.e. a full blank line on each side, not just
    ANSWER_KEY_MARKER's own single newline) -- both returned parts are
    stripped, so neither carries a stray leading/trailing blank line.
    Returns key=None if the marker is missing -- the caller treats that as
    a degraded-but-not-fatal result: the paper is still usable, just
    without a key."""
    marker = ANSWER_KEY_MARKER.strip()
    parts = re.split(rf"\n*{re.escape(marker)}\n*", text, maxsplit=1)
    if len(parts) != 2:
        return text, None
    return parts[0].strip(), parts[1].strip()


def generate_one_exam(
    llm_client,
    blueprint: dict,
    course_material: str,
    model: str,
    paper_number: int,
    count: int,
    raw_debug_path,
) -> dict:
    """Generate one exam paper + answer key. Returns
    {"error": bool, "paper_text": str|None, "key_text": str|None, "usage": ...};
    never raises."""
    user_prompt = build_exam_user_prompt(blueprint, course_material, paper_number, count)
    request_payload = {
        "model": model,
        "max_tokens": EXAM_MAX_TOKENS,
        "system": EXAM_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    try:
        response = llm_client.create_message(**request_payload)
    except LlmApiError as e:
        print(f"  [{paper_number}/{count}] exam: ERROR: {e}", file=sys.stderr)
        _write_json_atomic(
            raw_debug_path,
            {"prompt": request_payload, "response": None, "model": model, "usage": None, "error": str(e)},
        )
        return {"error": True, "paper_text": None, "key_text": None, "usage": None}

    text = response.text
    usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    paper_text, key_text = split_exam_and_key(text)
    if key_text is None:
        print(
            f"  [{paper_number}/{count}] exam: WARNING: no answer key marker in response -- key will be empty",
            file=sys.stderr,
        )

    _write_json_atomic(
        raw_debug_path,
        {
            "prompt": request_payload,
            "response": text,
            "model": model,
            "usage": usage,
            "key_found": key_text is not None,
        },
    )
    print(
        f"  [{paper_number}/{count}] exam: ok (input={usage['input_tokens']} output={usage['output_tokens']} tokens)"
    )
    return {"error": False, "paper_text": paper_text, "key_text": key_text, "usage": usage}


def _load_course_material() -> str | None:
    """Prefer the distilled essentials (shorter, already curated) over the
    full study guide -- matches process_course_essentials's own
    preference order in reverse (essentials is built FROM the guide, so
    once it exists it's the better exam-generation input)."""
    if COURSE_ESSENTIALS_PATH.exists():
        return COURSE_ESSENTIALS_PATH.read_text(encoding="utf-8")
    if STUDY_GUIDE_PATH.exists():
        return STUDY_GUIDE_PATH.read_text(encoding="utf-8")
    return None


def process_exam_generation(
    llm_client, count: int = 1, force: bool = False, questions_override: int | None = None
) -> bool:
    """Generate `count` practice exam papers (+ answer keys). Returns False
    only on a real failure: no course material to work from, format
    analysis failed, or any requested paper failed to generate."""
    course_material = _load_course_material()
    if course_material is None:
        print(
            f"[error] no course material found at {COURSE_ESSENTIALS_PATH} or {STUDY_GUIDE_PATH} "
            "(run stage 7, and optionally stage 10, first)",
            file=sys.stderr,
        )
        return False

    excerpt = course_material[:MAX_COURSE_MATERIAL_CHARS]
    blueprint = load_format_blueprint(llm_client, excerpt, force=force)
    if blueprint is None:
        return False

    prompt_blueprint = blueprint
    if questions_override is not None:
        prompt_blueprint = {**blueprint, "total_questions": questions_override}

    model = os.environ.get("EXAM_MODEL", DEFAULT_MODEL)
    failed = []
    for paper_number in range(1, count + 1):
        exam_path = OUTPUT_EXAMS_DIR / f"exam_{paper_number:02d}.md"
        key_path = OUTPUT_EXAMS_DIR / f"exam_{paper_number:02d}_key.md"
        raw_path = OUTPUT_EXAMS_DIR / f"exam_{paper_number:02d}_raw.json"

        if exam_path.exists() and not force:
            print(f"[skip] {exam_path} already exists (use --force to redo)")
            continue

        result = generate_one_exam(
            llm_client, prompt_blueprint, excerpt, model, paper_number, count, raw_path
        )
        if result["error"] or result["paper_text"] is None:
            failed.append(paper_number)
            continue

        _write_text_atomic(exam_path, result["paper_text"])
        _write_text_atomic(key_path, result["key_text"] or "*(no answer key was generated for this paper)*")

    if failed:
        print(f"FAILED: exam paper(s) {', '.join(str(n) for n in failed)}", file=sys.stderr)
        return False

    print(f"[done] wrote {count} exam paper(s) -> {OUTPUT_EXAMS_DIR}")
    return True
