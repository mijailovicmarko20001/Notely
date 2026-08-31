#!/usr/bin/env python3
"""
Stage [6]: Note Generation (LLM)
==================================

For each slide of a lecture, calls the Claude API with the slide's text,
speaker notes, and matched transcript chunk, and asks it to produce
structured per-slide study notes that preserve anything the professor said
that isn't on the slide.

Input:
    output/segmented_transcripts/<lecture_id>.json
        [{"slide_number": int, "slide_text": str, "notes_text": str,
          "transcript_text": str, "start": float, "end": float|None,
          "merged_from": [int]}, ...]

Output:
    output/notes/<lecture_id>.md
        Concatenated per-slide notes with a lecture header.
    output/notes/<lecture_id>_raw/slide_NNN.json
        Raw request/response pairing per slide, for debugging:
        {"prompt": ..., "response": ..., "model": ..., "usage": ...}
    output/notes/<lecture_id>_examples.json (only when NOTES_DETECT_EXAMPLES=1)
        Cached worked-example confirmation results:
        {"model": ..., "candidates_seen": int, "confirmed": [...], "rejected": [...],
         "usage": {"input_tokens": int, "output_tokens": int,
                    "cache_creation_input_tokens": int, "cache_read_input_tokens": int}}
    output/notes/<lecture_id>_raw/example_NNN.json (only when NOTES_DETECT_EXAMPLES=1)
        Raw request/response pairing per confirmed/rejected candidate.

Usage:
    python scripts/06_generate_notes.py <lecture_id>
    python scripts/06_generate_notes.py --all
    python scripts/06_generate_notes.py <lecture_id> --force

Config:
    ANTHROPIC_API_KEY     API key, loaded from .env if python-dotenv is available.
    NOTES_MODEL           Claude model id for note generation
                          (default: "claude-sonnet-5"). Set in .env.
    NOTES_DETECT_EXAMPLES Opt-in (off by default): confirm/caption stage 4/5's
                          worked-example candidates and embed them in the notes.
                          Set to "1" in .env.
    NOTES_EXAMPLES_MODEL  Claude model id for example confirmation
                          (default: "claude-haiku-4-5"). Set in .env.
    NOTES_EXAMPLES_MAX    Max example candidates confirmed per lecture
                          (default: 40). Set in .env.

Notes:
    - One API call per slide.
    - Slides with both empty slide_text and empty transcript_text are
      skipped (no API call) with a placeholder line in the output markdown.
    - A single failed slide (after the SDK's built-in retries are
      exhausted) does not abort the lecture: it gets an error placeholder
      in the markdown, is listed at the end of the file, and processing
      continues with the next slide.
    - `anthropic` is imported lazily (inside main) so that
      `python -m py_compile` and `--help` work even without the dependency
      installed.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Only needed to bootstrap the `from notely...` import below (finding
# notely/ on sys.path) -- notely.paths.PROJECT_ROOT is the same value and
# is what the rest of this file uses.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent

# notely/ (ports, paths) lives alongside scripts/ and webui/ at the
# project root, not on sys.path by default when this file is run directly
# (python scripts/06_generate_notes.py) -- same fix tests/conftest.py
# applies for test discovery. Must happen before the `from notely...`
# import below.
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.ports import LlmApiError  # noqa: E402
from notely.paths import PROJECT_ROOT  # noqa: E402

INPUT_SEGMENTED_DIR = PROJECT_ROOT / "output" / "segmented_transcripts"
OUTPUT_NOTES_DIR = PROJECT_ROOT / "output" / "notes"

DEFAULT_MODEL = "claude-sonnet-5"
# Safety ceiling only — sized well above any reasonable note so truncation
# never shapes output (brevity comes from the prompt, not the cap).
MAX_TOKENS = 8192

# Worked-example confirmation (opt-in via NOTES_DETECT_EXAMPLES, off by
# default -- see EXAMPLE_SYSTEM_PROMPT and process_lecture for the full
# feature). A cheap classify-and-caption task, so it defaults to Haiku
# rather than the notes model.
DEFAULT_EXAMPLES_MODEL = "claude-haiku-4-5"
DEFAULT_EXAMPLES_MAX_PER_LECTURE = 40
EXAMPLE_MAX_TOKENS = 256

SYSTEM_PROMPT = r"""You are generating condensed study notes for a university lecture slide, combining the text that was on the slide with what the professor said aloud while that slide was on screen.

For each slide you are given:
- the slide's title and body text (what was visually on the slide, from the original deck file)
- the slide's speaker notes, if any (author-written notes attached to the slide deck itself — NOT something the professor said aloud; use them only as background context)
- a transcript of what the professor said while this slide was displayed
- SOMETIMES an image: an actual screen capture of the slide as it was displayed during the lecture (distinct from the deck's own text above). If you receive this image, it may show the professor writing, drawing, circling, underlining, or otherwise annotating the slide live — content that exists ONLY in that image, nowhere in the deck text. Treat anything visible in the image that is NOT already covered by the given slide text as professor-added content, exactly like something said aloud: fold it into "Professor's notes" (a formula the professor derived on the slide, a diagram they sketched, a term they circled for emphasis, a correction they wrote over the original text). If the image just matches the printed slide with nothing added, or you did not receive an image for this slide, say nothing about the image itself — never mention "the image" or "the screenshot" as a thing in the note.

Your job:
- Preserve anything the professor said or wrote that is NOT on the slide: examples, clarifications, corrections, asides like "this will be on the exam," edge cases, and anything hand-written/drawn live per the image rule above. This is the most valuable part of the note — do not drop it or bury it.
- Keep definitions and formulas exact. Do not loosely paraphrase a definition or formula — reproduce it precisely as given (on the slide or spoken), correcting it only if the professor explicitly corrects the slide.
- Every formula, without exception, must be written as proper LaTeX math: `$...$` for a short inline expression (e.g. a single symbol or variable referenced in a sentence), `$$...$$` on its own line for a standalone equation or derivation step. Never render a formula as plain text or with unicode math symbols substituting for LaTeX commands (no "∫ from −∞ to +∞ of ...", no bare "x_a(t)" outside math delimiters, no unicode sub/superscript characters like ₋ⁿ) — always use real LaTeX commands (`\int`, `\sum`, `\infty`, `_{...}`, `^{...}`, `\delta`, `\pi`, etc.) inside `$`/`$$` delimiters instead. Extend this to slide-provided OCR'd formulas too: if the slide text contains a garbled or unicode-notation version of a formula, transcribe it into clean LaTeX rather than reproducing the garbling.
- Do not just restate the slide's bullets in different words — that adds no value over the slide itself. The reader already sees the slide's own text and (usually) its image directly next to this note; your job is to add what isn't already visible there, not to transcribe it.
- The transcript is automatic speech-recognition output and may mis-transcribe technical terms, names, and abbreviations. When a transcript word is clearly a garbled version of a term appearing in the slide text, use the slide's spelling; never carry an obvious ASR artifact into the note.
- Output structured markdown:
  - The slide title as a `##` heading.
  - Key points from the slide as a bullet list — ONLY if condensing genuinely helps (e.g. distilling a dense paragraph, imposing structure on unstructured prose, pulling a formula out of running text). If the slide's own text is already short, already a clean bullet list, or self-explanatory (title slides, section breaks, a single image/table, a short list that's already scannable), SKIP this section entirely rather than re-typing it in slightly different words.
  - A `**Professor's notes:**` subsection containing only content the professor added beyond the printed slide — verbally (examples, clarifications, corrections, exam hints, edge cases) or by writing/drawing on the slide live (see the image rule above). Do not put slide-only content in this subsection.
- If the transcript adds nothing beyond the slide AND the slide's own text needs no condensing, the entire note body is just one line: "*No additional commentary beyond the slide.*" — do not also emit a Key points list restating the slide in that case. This is the expected, correct output for plenty of slides (title slides, quick flip-throughs, section breaks) — a one-line note is not a failure to be padded out, it's doing its job.
- SOMETIMES you are also given a "Worked examples" list: worked examples the professor did while this slide was on screen, already confirmed and captioned, and already embedded as images with their captions directly below your note, under their OWN `**Examples:**` heading that the pipeline adds automatically (not you). Do NOT create an `**Examples:**` (or translated equivalent, e.g. `**Primeri:**`) heading yourself — one already follows your note verbatim, and a second one would be a confusing duplicate. Instead, fold what each example demonstrates and its key result or takeaway into `**Professor's notes:**`, referencing them in the order given (e.g. "In the first example, ..."). Never emit a markdown image link yourself for an example, and never say "the image"/"the screenshot" here either, for the same reason as the image rule above: the actual capture is already shown to the reader right below.
- Be concise. This is a study aid meant to be read in minutes, not a transcript.
- LANGUAGE: write the entire note in the same language as the lecture content (the slide text and transcript). Never translate it and never mix languages within a note — if the lecture is in Serbian, every sentence you write is in Serbian (LaTeX, standard abbreviations like ADC/SNR, and the literal subsection label "Professor's notes:" are the only exceptions).
- LaTeX hygiene: every `$`/`$$` delimiter must be balanced and the expression inside must be valid, compilable LaTeX. Never nest `$` inside `$$`, never leave a lone `$`, and never mix unicode math symbols into a LaTeX expression."""

EXAMPLE_SYSTEM_PROMPT = r"""You are screening a single video frame captured during a university lecture to decide whether the professor is working through a CONCRETE EXAMPLE OR EXERCISE **in this exact frame**, as opposed to just displaying a slide with no added work.

You are given:
- the frame image itself (a screen capture from the lecture recording) — THIS IS THE ONLY EVIDENCE for is_example. Base your verdict strictly on what is visibly on screen in this image.
- the slide's own text (title + body) — context for what deck page this is, nothing more.
- a snippet of transcript from roughly the same moment, and any spoken cue words already detected in it — context ONLY, for understanding what topic is being discussed. NEVER use the transcript as evidence that an example is present. The professor may be introducing an example, mid-example, or wrapping one up while an entirely different (or no) frame is on screen; a transcript segment that talks about an example proves nothing about what this specific frame shows.

A CONCRETE EXAMPLE means: a specific numeric calculation, a worked derivation, a diagram or circuit sketched out, a step-by-step solution to a stated problem — something a student would want to see and follow, not just an abstract restatement of the slide's own text. If the frame is just the printed slide with no visible added work, or the visible content duplicates the slide's own text almost verbatim, it is NOT an example for this purpose — even if the transcript at this timestamp is actively discussing an example.

A title slide, a bullet-point list, or a section header is never an example, regardless of what comes later in the same run or what the transcript says is coming. If you find yourself wanting to say "the transcript suggests an example is being shown around here" — that is exactly the reasoning to reject: judge only the pixels in front of you.

Respond with ONLY a JSON object, no other text, no markdown fences, no explanation:
{"is_example": true|false, "kind": "whiteboard"|"annotated_slide"|"example_slide", "caption": "...", "confidence": 0.0-1.0}

- "kind": classify what you actually SEE, regardless of which heuristic flagged the frame — "annotated_slide" if it's a printed slide with live writing/drawing on it, "whiteboard" if the screen shows a whiteboard/tablet/scratch page unrelated to any printed slide, "example_slide" if it's a printed slide whose own content already is a worked example with nothing added live.
- "caption": ONE short sentence, in the SAME LANGUAGE as the transcript/slide text, describing ONLY what is visibly demonstrated in THIS image (e.g. "Worked example: computing SNR for a 12-bit ADC"). Never describe content the transcript mentions but this image does not itself show. Empty string if is_example is false.
- "confidence": your confidence that is_example is correctly classified, 0.0-1.0.
- If you cannot tell from the image, or the image itself doesn't clearly show worked content, set is_example to false rather than guessing or inferring from the transcript."""


def load_dotenv_if_available() -> None:
    """Best-effort .env loading; never fatal if python-dotenv isn't installed."""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass


def _write_json_atomic(path: Path, data) -> None:
    """Write via a temp file + atomic rename so a killed process never
    leaves a truncated-but-non-empty file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def _write_text_atomic(path: Path, text: str) -> None:
    """Same guarantee as _write_json_atomic, for the final notes/<id>.md —
    the one file webui/progress.py::artifact_ok checks to decide stage 6
    is done and skippable on a later run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    tmp.replace(path)


# Anthropic's own recommended long-edge max for vision inputs -- larger
# just wastes bandwidth/latency without a quality gain (the API downscales
# past this anyway). Frame captures from stage 3 are full video resolution
# cropped to the slide region, easily larger than this.
FRAME_IMAGE_MAX_DIM = 1568

# The example-confirmation call is a classify-and-caption task, not a
# careful transcription of small print, so it can use a smaller image than
# note generation's own vision path -- roughly halves image tokens on that
# (Haiku-priced) call.
EXAMPLE_IMAGE_MAX_DIM = 1024


def load_frame_image_b64(frame_image_path: str, max_dim: int = FRAME_IMAGE_MAX_DIM) -> str | None:
    """Load, downscale if needed, and base64-encode a video frame for the
    vision API. Returns None (never raises) on any failure -- a missing or
    unreadable frame should fall back to text-only for that slide, not
    fail note generation. frame_image_path is relative to PROJECT_ROOT,
    the same convention stage 3/4 already use."""
    import base64
    import io
    from PIL import Image

    path = PROJECT_ROOT / frame_image_path
    if not path.exists():
        return None
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            if max(img.size) > max_dim:
                scale = max_dim / max(img.size)
                new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
                img = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:  # noqa: BLE001 - best-effort, never fatal
        print(f"  warning: failed to load frame image {path}: {e}", file=sys.stderr)
        return None


def fmt_ts(seconds: float) -> str:
    """Format a timestamp in seconds as M:SS, or H:MM:SS past an hour."""
    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def frame_md_path(frame_rel: str) -> str:
    """Rewrite a PROJECT_ROOT-relative frame path (e.g.
    "output/frame_events/<id>_frames/event_NNN.png") into the relative path
    notes/<id>.md needs to reach it: notes live one level under output/,
    same as the slides_extracted embed, so strip the leading "output/" and
    point one directory up."""
    return "../" + "/".join(Path(frame_rel).parts[1:])


def add_slide_number_to_heading(note_text: str, slide_number) -> str:
    """
    Deterministically prefix the note's first `##` heading with its slide
    number (e.g. "## Slide 19: Odabiranje signala...").

    This is done here rather than left to the model's own prompt compliance:
    the system prompt just says "slide title as heading", and in practice
    the model included a slide number in front of the title inconsistently
    (occasionally, not reliably). Without it, a deck with several
    consecutive/revisited slides sharing the same or a near-identical title
    (common in a multi-slide formula derivation, or a slide the professor
    revisits later) produces headings that are indistinguishable from each
    other and impossible to trace back to slide_timeline.json.
    """
    lines = note_text.split("\n", 1)
    heading, rest = lines[0], (lines[1] if len(lines) > 1 else "")

    if not heading.startswith("## "):
        return note_text  # model didn't follow the heading format; leave as-is

    title = heading[3:].strip()
    if re.match(rf"^Slide\s+{re.escape(str(slide_number))}\b", title):
        return note_text  # model already included this exact slide number

    new_heading = f"## Slide {slide_number}: {title}"
    return new_heading + ("\n" + rest if rest else "")


def build_user_prompt(slide: dict, confirmed_examples: list[dict] | None = None) -> str:
    """Build the per-slide user turn from a segmented_transcripts entry.

    `confirmed_examples`, when non-empty, adds a trailing section listing
    the worked examples stage 6's own confirmation pass found for this
    slide (see run_example_confirmation) -- see SYSTEM_PROMPT's "Worked
    examples" clause for how the model is told to use it.
    """
    slide_number = slide.get("slide_number")
    slide_text = (slide.get("slide_text") or "").strip()
    notes_text = (slide.get("notes_text") or "").strip()
    transcript_text = (slide.get("transcript_text") or "").strip()

    prompt = (
        f"Slide number: {slide_number}\n\n"
        f"Slide text (what was on screen):\n{slide_text or '(none)'}\n\n"
        f"Speaker notes (from the deck itself, NOT something the professor said aloud):\n"
        f"{notes_text or '(none)'}\n\n"
        f"Transcript (what the professor said while this slide was on screen):\n"
        f"{transcript_text or '(none)'}"
    )

    if confirmed_examples:
        lines = [
            "\n\nWorked examples the professor did while this slide was on screen "
            "(already embedded as images in the note, in this order):"
        ]
        for i, ex in enumerate(confirmed_examples, start=1):
            lines.append(
                f"{i}. [{ex['kind']} @ {fmt_ts(ex['timestamp'])}] {ex.get('caption') or '(no caption)'}"
            )
        prompt += "\n".join(lines)

    return prompt


def generate_slide_note(
    client,
    model: str,
    slide: dict,
    index: int,
    total: int,
    raw_dir: Path,
    send_frame_image: bool = False,
    confirmed_examples: list[dict] | None = None,
) -> dict:
    """Generate notes for a single slide. Returns a result dict; never raises."""
    slide_number = slide.get("slide_number", index)
    slide_text = (slide.get("slide_text") or "").strip()
    transcript_text = (slide.get("transcript_text") or "").strip()

    if not slide_text and not transcript_text:
        print(f"  [{index}/{total}] slide {slide_number}: skipped (no slide text or transcript)")
        return {"skipped": True, "error": False, "slide_number": slide_number}

    user_prompt = build_user_prompt(slide, confirmed_examples)

    # NOTES_SEND_FRAME_IMAGE (opt-in, off by default -- real added cost):
    # attach the actual on-screen capture of this slide, not just its
    # extracted deck text, so live annotations (writing/drawing the deck
    # file itself never had) reach the model. See SYSTEM_PROMPT's image
    # rule for how it's used, and DOCUMENTATION.md for the full feature.
    frame_attached = False
    content = []
    if send_frame_image:
        frame_path = slide.get("frame_image_path")
        if frame_path:
            b64 = load_frame_image_b64(frame_path)
            if b64:
                content.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": b64},
                    }
                )
                frame_attached = True
    content.append({"type": "text", "text": user_prompt})

    request_payload = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        # cache_control makes the (identical, every-slide, every-lecture)
        # system prompt a prompt-cache breakpoint: the first call in a
        # session pays full price to write the cache, every subsequent
        # call within the ~5min ephemeral TTL reads it at a fraction of the
        # input-token cost. Safe to always set regardless of the prompt's
        # exact token count -- the API silently skips caching (no error)
        # for blocks under its minimum cacheable size rather than
        # rejecting the request.
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
    }

    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"slide_{slide_number:03d}.json"

    # A sanitized copy for the debug dump: the real request_payload's base64
    # image data would otherwise bloat every raw/slide_NNN.json file by
    # however large the (downscaled) frame PNG is -- record that one was
    # attached and which frame, not the bytes themselves.
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
        response = client.create_message(**request_payload)
    except LlmApiError as e:
        print(f"  [{index}/{total}] slide {slide_number}: ERROR: {e}", file=sys.stderr)
        _write_json_atomic(
            raw_path,
            {
                "prompt": debug_prompt,
                "response": None,
                "model": model,
                "usage": None,
                "error": str(e),
                "frame_image_attached": frame_attached,
            },
        )
        return {
            "skipped": False,
            "error": True,
            "slide_number": slide_number,
            "message": str(e),
        }

    note_text = response.text

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": response.usage.cache_creation_input_tokens,
        "cache_read_input_tokens": response.usage.cache_read_input_tokens,
    }

    _write_json_atomic(
        raw_path,
        {
            "prompt": debug_prompt,
            "response": note_text,
            "model": model,
            "usage": usage,
            "frame_image_attached": frame_attached,
        },
    )

    cache_note = (
        f" cache_read={usage['cache_read_input_tokens']}"
        if usage["cache_read_input_tokens"]
        else f" cache_write={usage['cache_creation_input_tokens']}"
        if usage["cache_creation_input_tokens"]
        else ""
    )
    frame_note = " +frame_image" if frame_attached else ""
    print(
        f"  [{index}/{total}] slide {slide_number}: ok{frame_note} "
        f"(input={usage['input_tokens']} output={usage['output_tokens']} tokens{cache_note})"
    )

    return {
        "skipped": False,
        "error": False,
        "slide_number": slide_number,
        "text": note_text,
        "usage": usage,
        "frame_image_attached": frame_attached,
    }


# --- Worked-example confirmation (opt-in, NOTES_DETECT_EXAMPLES) -----------
# Stage 4/5 flag candidate frames with cheap local heuristics (OCR/TF-IDF
# score, dHash ink drift, slide-title keyword match); this phase asks a
# vision model to confirm each candidate is actually a worked example and
# to caption it, before it's embedded in the note markdown. See
# EXAMPLE_SYSTEM_PROMPT for the confirmation prompt and CLAUDE.md for the
# full feature.

EXAMPLE_KIND_PRIORITY = {"whiteboard": 0, "annotated_slide": 1, "example_slide": 2}


def build_example_prompt(candidate: dict, slide: dict) -> str:
    """Build the per-candidate user turn for the confirmation call."""
    slide_text = (slide.get("slide_text") or "").strip()
    context = (candidate.get("transcript_context") or "").strip()
    cue_hits = candidate.get("cue_hits") or []

    detected_by = f"Detected via: {candidate['kind']} heuristic (match score={candidate['score']:.3f}"
    if candidate.get("ink_delta") is not None:
        detected_by += f", ink_delta={candidate['ink_delta']}"
    detected_by += ")"

    return (
        f"{detected_by}\n\n"
        f"Slide text (what was printed on screen):\n{slide_text or '(none)'}\n\n"
        f"Transcript around this moment (CONTEXT ONLY -- do not use this as evidence for "
        f"is_example; judge only the image):\n{context or '(none)'}\n\n"
        f"Spoken example cues detected nearby (CONTEXT ONLY, same caveat): "
        f"{', '.join(cue_hits) if cue_hits else '(none)'}"
    )


def parse_example_verdict(text: str) -> dict | None:
    """Parse the confirmation model's JSON verdict, tolerating a fenced
    code block or prose wrapped around the JSON object. Returns None on
    anything that doesn't parse or is missing the required field -- the
    caller treats that as "not an example," since a parse failure must
    never break note generation."""
    if not text:
        return None

    candidate_text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate_text, re.DOTALL)
    if fence_match:
        candidate_text = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", candidate_text, re.DOTALL)
        if brace_match:
            candidate_text = brace_match.group(0)

    try:
        data = json.loads(candidate_text)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict) or "is_example" not in data:
        return None

    try:
        confidence = float(data.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "is_example": bool(data.get("is_example")),
        "kind": data.get("kind") or "annotated_slide",
        "caption": (data.get("caption") or "").strip(),
        "confidence": confidence,
    }


def sum_usage(usages) -> dict:
    """Sum an iterable of per-call usage dicts (as returned by confirm_example
    or generate_slide_note), skipping None entries -- a call that errored
    before a response arrived has no usage to add. Same four fields both
    call sites already track, so one summer works for either."""
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    for usage in usages:
        if not usage:
            continue
        for key in totals:
            totals[key] += usage.get(key, 0) or 0
    return totals


def rank_example_candidates(pairs: list[tuple[dict, dict]]) -> list[tuple[dict, dict]]:
    """Order (slide, candidate) pairs by how worth confirming they are, so
    truncating to NOTES_EXAMPLES_MAX keeps the strongest evidence rather
    than whatever stage 5 happened to attach first: a candidate with a
    spoken example cue first, then by kind (a whiteboard switch is
    stronger evidence than ink drift on a slide, which is stronger than a
    deck's own "example"-titled slide with nothing added live), then
    chronologically."""

    def key(pair):
        _, candidate = pair
        return (
            0 if candidate.get("cue_hits") else 1,
            EXAMPLE_KIND_PRIORITY.get(candidate["kind"], 99),
            candidate["timestamp"],
        )

    return sorted(pairs, key=key)


def confirm_example(
    client,
    model: str,
    slide: dict,
    candidate: dict,
    index: int,
    total: int,
    raw_dir: Path,
) -> dict:
    """Ask the confirmation model whether one candidate frame is actually a
    worked example, and caption it if so. Returns a result dict; never
    raises -- a failed or unparseable call is treated as "not an example"
    rather than aborting the lecture."""
    frame_path = candidate.get("frame_image_path")
    b64 = load_frame_image_b64(frame_path, max_dim=EXAMPLE_IMAGE_MAX_DIM) if frame_path else None

    content = []
    if b64:
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            }
        )
    content.append({"type": "text", "text": build_example_prompt(candidate, slide)})

    request_payload = {
        "model": model,
        "max_tokens": EXAMPLE_MAX_TOKENS,
        # Deterministic, not just low-variance: on ambiguous frames (e.g. two
        # near-identical consecutive frames of the same slide, one with the
        # real content and one without) this is a genuinely hard call, and a
        # flip-flopping verdict across runs is worse than a consistently-
        # applied one -- especially since confirmed/rejected results are
        # cached to disk (see NOTES_DETECT_EXAMPLES) and meant to be stable
        # across re-runs of the *notes* prompt. Haiku 4.5 still accepts a
        # fixed sampling temperature (removed only on the Opus/Sonnet 4.6+
        # family), so pin it to 0 here rather than leaving it default.
        "temperature": 0,
        "system": [{"type": "text", "text": EXAMPLE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
    }

    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"example_{candidate['event_index']:03d}.json"

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

    base = {
        "event_index": candidate["event_index"],
        "timestamp": candidate["timestamp"],
        "frame_image_path": frame_path,
        "slide_number": candidate["slide_number"],
        "detected_kind": candidate["kind"],
    }

    try:
        response = client.create_message(**request_payload)
    except LlmApiError as e:
        print(f"  [{index}/{total}] example @{fmt_ts(candidate['timestamp'])}: ERROR: {e}", file=sys.stderr)
        _write_json_atomic(
            raw_path,
            {"prompt": debug_prompt, "response": None, "model": model, "error": str(e)},
        )
        return {**base, "error": False, "is_example": False}

    text = response.text
    verdict = parse_example_verdict(text)

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": response.usage.cache_creation_input_tokens,
        "cache_read_input_tokens": response.usage.cache_read_input_tokens,
    }

    _write_json_atomic(
        raw_path,
        {"prompt": debug_prompt, "response": text, "model": model, "usage": usage, "verdict": verdict},
    )

    if verdict is None:
        print(
            f"  [{index}/{total}] example @{fmt_ts(candidate['timestamp'])}: unparseable verdict, treated as rejected"
        )
        return {**base, "error": False, "is_example": False, "usage": usage}

    status = "confirmed" if verdict["is_example"] else "rejected"
    print(f"  [{index}/{total}] example @{fmt_ts(candidate['timestamp'])}: {status} ({verdict['kind']})")

    return {**base, "error": False, "usage": usage, **verdict}


def run_example_confirmation(
    client,
    model: str,
    slides: list[dict],
    raw_dir: Path,
    max_candidates: int,
    concurrency: int,
) -> tuple[list[dict], list[dict], int]:
    """Confirm and caption a lecture's worked-example candidates via the
    vision model. Returns (confirmed, rejected, candidates_seen);
    confirmed/rejected entries carry enough to attach back to a slide
    (slide_number, timestamp, frame_image_path, kind, caption) and to
    debug a bad classification (detected_kind vs the confirmed kind)."""
    pairs = [(slide, candidate) for slide in slides for candidate in slide.get("example_candidates", [])]
    candidates_seen = len(pairs)
    if not pairs:
        return [], [], 0

    ranked = rank_example_candidates(pairs)[:max_candidates]
    total = len(ranked)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [
            pool.submit(confirm_example, client, model, slide, candidate, i, total, raw_dir)
            for i, (slide, candidate) in enumerate(ranked, start=1)
        ]
        results = [f.result() for f in futures]

    confirmed = [r for r in results if r.get("is_example")]
    rejected = [r for r in results if not r.get("is_example")]
    return confirmed, rejected, candidates_seen


def build_examples_markdown(confirmed_examples: list[dict]) -> str:
    """Render a slide's confirmed worked examples as a trailing
    '**Examples:**' section: the actual on-screen frame image plus the
    confirmation model's one-line caption, in chronological order. Returns
    '' if there's nothing to embed (no confirmed examples, or their frame
    files are missing)."""
    blocks = []
    for i, ex in enumerate(confirmed_examples, start=1):
        frame_path = ex.get("frame_image_path")
        if not frame_path or not (PROJECT_ROOT / frame_path).exists():
            continue
        md_path = frame_md_path(frame_path)
        ts = fmt_ts(ex["timestamp"])
        caption = (ex.get("caption") or "").strip()
        block = f"![Example {i} — {ts}]({md_path})"
        if caption:
            block += f"\n*{caption}*"
        blocks.append(block)

    if not blocks:
        return ""
    return "\n\n**Examples:**\n\n" + "\n\n".join(blocks) + "\n"


def process_lecture(llm_client, lecture_id: str, force: bool = False) -> bool:
    """Generate notes for every slide of a lecture and write the notes markdown."""
    input_path = INPUT_SEGMENTED_DIR / f"{lecture_id}.json"
    output_path = OUTPUT_NOTES_DIR / f"{lecture_id}.md"
    raw_dir = OUTPUT_NOTES_DIR / f"{lecture_id}_raw"

    if not input_path.exists():
        print(f"[skip] {lecture_id}: no segmented transcript found at {input_path}", file=sys.stderr)
        return False

    if output_path.exists() and not force:
        print(f"[skip] {lecture_id}: notes already exist at {output_path} (use --force to redo)")
        return True

    try:
        with open(input_path, encoding="utf-8") as f:
            slides = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[error] {lecture_id}: failed to read {input_path}: {e}", file=sys.stderr)
        return False

    model = os.environ.get("NOTES_MODEL", DEFAULT_MODEL)
    client = llm_client

    # Opt-in, off by default -- a real added cost (vision tokens on every
    # slide call), not a free win, so this doesn't happen silently. See
    # SYSTEM_PROMPT's image rule and DOCUMENTATION.md for the full feature.
    send_frame_image = os.environ.get("NOTES_SEND_FRAME_IMAGE", "0") == "1"

    # Opt-in, off by default -- same reasoning as NOTES_SEND_FRAME_IMAGE: a
    # real added cost (one vision call per candidate frame), so it doesn't
    # happen silently. See EXAMPLE_SYSTEM_PROMPT and run_example_confirmation
    # for the full feature.
    detect_examples = os.environ.get("NOTES_DETECT_EXAMPLES", "0") == "1"
    examples_model = os.environ.get("NOTES_EXAMPLES_MODEL", DEFAULT_EXAMPLES_MODEL)
    examples_max = max(0, int(os.environ.get("NOTES_EXAMPLES_MAX", str(DEFAULT_EXAMPLES_MAX_PER_LECTURE))))
    examples_cache_path = OUTPUT_NOTES_DIR / f"{lecture_id}_examples.json"

    confirmed_by_slide_number: dict = {}
    examples_usage = sum_usage([])
    if detect_examples:
        if examples_cache_path.exists() and not force:
            with open(examples_cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            confirmed_list = cached.get("confirmed", [])
            # Older caches (written before usage tracking was added) simply
            # won't have this key -- report zeros rather than crashing.
            examples_usage = cached.get("usage") or examples_usage
            print(
                f"[{lecture_id}] using cached example confirmations -> {examples_cache_path} "
                f"({len(confirmed_list)} confirmed)"
            )
        else:
            print(f"[{lecture_id}] confirming worked-example candidates with model={examples_model}...")
            confirmed_list, rejected_list, candidates_seen = run_example_confirmation(
                client,
                examples_model,
                slides,
                raw_dir,
                examples_max,
                concurrency=max(1, int(os.environ.get("NOTES_CONCURRENCY", "4"))),
            )
            examples_usage = sum_usage(r.get("usage") for r in confirmed_list + rejected_list)
            _write_json_atomic(
                examples_cache_path,
                {
                    "model": examples_model,
                    "candidates_seen": candidates_seen,
                    "confirmed": confirmed_list,
                    "rejected": rejected_list,
                    "usage": examples_usage,
                },
            )
            cache_note = (
                f" cache_read={examples_usage['cache_read_input_tokens']}"
                if examples_usage["cache_read_input_tokens"]
                else ""
            )
            print(
                f"[{lecture_id}] example confirmation: {len(confirmed_list)}/{candidates_seen} confirmed -> "
                f"{examples_cache_path} (tokens: input={examples_usage['input_tokens']} "
                f"output={examples_usage['output_tokens']}{cache_note})"
            )
        for ex in confirmed_list:
            confirmed_by_slide_number.setdefault(ex["slide_number"], []).append(ex)
        for exs in confirmed_by_slide_number.values():
            exs.sort(key=lambda ex: ex["timestamp"])

    total = len(slides)
    frame_note = " (sending on-screen frame images to the model)" if send_frame_image else ""
    print(f"[{lecture_id}] generating notes for {total} slide(s) with model={model}{frame_note}")

    md_parts = [f"# {lecture_id}\n"]
    failed_slides = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_write_tokens = 0
    total_cache_read_tokens = 0
    frames_attached = 0

    # Per-slide calls are independent, so issue them concurrently — the
    # stage's wall time is pure API latency otherwise. Results are collected
    # by index so note order in the markdown is unaffected. NOTES_CONCURRENCY
    # tunes the pool (lower it if you hit API rate limits; the SDK's
    # max_retries=5 already absorbs occasional 429s).
    from concurrent.futures import ThreadPoolExecutor

    concurrency = max(1, int(os.environ.get("NOTES_CONCURRENCY", "4")))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(
                generate_slide_note,
                client,
                model,
                slide,
                i,
                total,
                raw_dir,
                send_frame_image=send_frame_image,
                confirmed_examples=confirmed_by_slide_number.get(slide.get("slide_number")),
            )
            for i, slide in enumerate(slides, start=1)
        ]
        results = [f.result() for f in futures]  # index-aligned with slides

    # Present slides numbered 1..N in the order they appeared in the lecture.
    # Deck-internal numbers are meaningless to a reader (pooled/merged decks
    # make them huge and non-contiguous); they survive only in the embedded
    # image path and the *_raw/ debug files.
    embed_images = os.environ.get("NOTES_EMBED_IMAGES", "1") != "0"
    images_dir = OUTPUT_NOTES_DIR.parent / "slides_extracted" / f"{lecture_id}_images"

    for position, (slide, result) in enumerate(zip(slides, results, strict=True), start=1):
        deck_number = slide.get("slide_number", position)

        image_line = ""
        if embed_images:
            image_path = images_dir / f"slide_{deck_number:03d}.png"
            if image_path.exists():
                image_line += (
                    f"\n![slide {position}]"
                    f"(../slides_extracted/{lecture_id}_images/slide_{deck_number:03d}.png)\n"
                )
        # The actual on-screen capture (may show live annotations the deck
        # render above can't), embedded alongside it -- same flag that
        # controls whether the model saw it, since if we're already paying
        # to fetch/encode the frame, showing it to a human reader too is
        # free. frame_image_path is relative to PROJECT_ROOT (e.g.
        # "output/frame_events/<id>_frames/event_NNN.png"); notes live one
        # level under output/, same as the slides_extracted embed above, so
        # strip the leading "output/" and point one directory up.
        if send_frame_image:
            frame_rel = slide.get("frame_image_path")
            if frame_rel and (PROJECT_ROOT / frame_rel).exists():
                image_line += (
                    f"\n![slide {position} as shown during the lecture]({frame_md_path(frame_rel)})\n"
                )

        examples_md = build_examples_markdown(confirmed_by_slide_number.get(deck_number, []))

        if result["skipped"]:
            md_parts.append(
                f"## Slide {position}\n{image_line}\n*(No slide text or transcript available — skipped.)*\n{examples_md}"
            )
            continue

        if result["error"]:
            failed_slides.append(deck_number)
            md_parts.append(
                f"## Slide {position}\n{image_line}\n"
                f"*(Note generation failed for this slide: {result['message']})*\n{examples_md}"
            )
            continue

        usage = result["usage"]
        total_input_tokens += usage["input_tokens"]
        total_output_tokens += usage["output_tokens"]
        total_cache_write_tokens += usage.get("cache_creation_input_tokens", 0)
        total_cache_read_tokens += usage.get("cache_read_input_tokens", 0)
        if result.get("frame_image_attached"):
            frames_attached += 1
        note = add_slide_number_to_heading(result["text"], position)
        if image_line:
            # image goes directly under the slide's heading line
            heading, _, rest = note.partition("\n")
            note = heading + "\n" + image_line + rest
        md_parts.append(note + "\n" + examples_md)

    # Lecture-level overview, generated from the finished per-slide notes and
    # placed right under the lecture title.
    try:
        summary_prompt = (
            "Below are the complete per-slide study notes for one university lecture. "
            "Write a lecture overview in THE SAME LANGUAGE as the notes, as markdown, with:\n"
            "- a `## Pregled predavanja` heading (translate the heading into the notes' language "
            "if it is not Serbian),\n"
            "- 5-10 bullets naming the key concepts and how they connect,\n"
            "- a final bullet list of anything exam-relevant the professor emphasized "
            "(omit it if there is none).\n"
            "Be brief — this is an orientation map, not a second copy of the notes.\n\n"
            + "\n".join(md_parts)[:60000]
        )
        response = client.create_message(
            model=model,
            max_tokens=8192,  # safety ceiling only — 1024 truncated overviews mid-sentence
            messages=[{"role": "user", "content": summary_prompt}],
        )
        summary_text = response.text
        if summary_text:
            md_parts.insert(1, summary_text + "\n\n---\n")
            print(f"[{lecture_id}] lecture overview generated")
    except LlmApiError as e:
        print(f"[{lecture_id}] WARNING: lecture overview failed: {e}", file=sys.stderr)

    if failed_slides:
        md_parts.append(
            "\n---\n\n**Slides that failed to generate:** " + ", ".join(str(n) for n in failed_slides) + "\n"
        )

    _write_text_atomic(output_path, "\n".join(md_parts))

    status = f" ({len(failed_slides)} slide(s) failed)" if failed_slides else ""
    cache_summary = (
        f" cache_write={total_cache_write_tokens} cache_read={total_cache_read_tokens}"
        if (total_cache_write_tokens or total_cache_read_tokens)
        else ""
    )
    frames_summary = f" frame_images={frames_attached}/{total}" if send_frame_image else ""
    examples_summary = (
        f" examples={sum(len(exs) for exs in confirmed_by_slide_number.values())} "
        f"(example-confirm tokens: input={examples_usage['input_tokens']} "
        f"output={examples_usage['output_tokens']} cache_read={examples_usage['cache_read_input_tokens']})"
        if detect_examples
        else ""
    )
    print(
        f"[done] {lecture_id}: wrote notes -> {output_path}{status} "
        f"(total tokens: input={total_input_tokens} output={total_output_tokens}{cache_summary}{frames_summary}{examples_summary})"
    )

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Stage [6]: Generate per-slide study notes via the Claude API."
    )
    parser.add_argument(
        "lecture_id",
        nargs="?",
        default=None,
        help="Lecture id, e.g. lecture01 (matches output/segmented_transcripts/<lecture_id>.json)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate notes for every segmented transcript found in output/segmented_transcripts/",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if output/notes/<lecture_id>.md already exists",
    )
    args = parser.parse_args()

    if bool(args.all) == bool(args.lecture_id):
        parser.error("provide exactly one of <lecture_id> or --all")

    load_dotenv_if_available()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set (check .env)", file=sys.stderr)
        sys.exit(1)

    # lazy import (via the adapter) — keeps py_compile / --help working
    # without the anthropic package installed
    from notely.adapters.anthropic_llm import AnthropicLlmClient

    llm_client = AnthropicLlmClient(max_retries=5)

    if args.all:
        input_files = sorted(INPUT_SEGMENTED_DIR.glob("*.json"))
        if not input_files:
            print(f"No segmented transcripts found in {INPUT_SEGMENTED_DIR}")
            return
        lecture_ids = [p.stem for p in input_files]
    else:
        lecture_ids = [args.lecture_id]

    failed = []
    for lecture_id in lecture_ids:
        ok = process_lecture(llm_client, lecture_id, force=args.force)
        if not ok:
            failed.append(lecture_id)

    if failed:
        print(f"FAILED: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
