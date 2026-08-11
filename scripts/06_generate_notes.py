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

Usage:
    python scripts/06_generate_notes.py <lecture_id>
    python scripts/06_generate_notes.py --all
    python scripts/06_generate_notes.py <lecture_id> --force

Config:
    ANTHROPIC_API_KEY  API key, loaded from .env if python-dotenv is available.
    NOTES_MODEL        Claude model id for note generation
                        (default: "claude-sonnet-5"). Set in .env.

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

# Project root = parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_SEGMENTED_DIR = PROJECT_ROOT / "output" / "segmented_transcripts"
OUTPUT_NOTES_DIR = PROJECT_ROOT / "output" / "notes"

DEFAULT_MODEL = "claude-sonnet-5"
# Safety ceiling only — sized well above any reasonable note so truncation
# never shapes output (brevity comes from the prompt, not the cap).
MAX_TOKENS = 8192

SYSTEM_PROMPT = r"""You are generating condensed study notes for a university lecture slide, combining the text that was on the slide with what the professor said aloud while that slide was on screen.

For each slide you are given:
- the slide's title and body text (what was visually on the slide)
- the slide's speaker notes, if any (author-written notes attached to the slide deck itself — NOT something the professor said aloud; use them only as background context)
- a transcript of what the professor said while this slide was displayed

Your job:
- Preserve anything the professor said that is NOT on the slide: examples, clarifications, corrections, asides like "this will be on the exam," and edge cases. This is the most valuable part of the note — do not drop it or bury it.
- Keep definitions and formulas exact. Do not loosely paraphrase a definition or formula — reproduce it precisely as given (on the slide or spoken), correcting it only if the professor explicitly corrects the slide.
- Every formula, without exception, must be written as proper LaTeX math: `$...$` for a short inline expression (e.g. a single symbol or variable referenced in a sentence), `$$...$$` on its own line for a standalone equation or derivation step. Never render a formula as plain text or with unicode math symbols substituting for LaTeX commands (no "∫ from −∞ to +∞ of ...", no bare "x_a(t)" outside math delimiters, no unicode sub/superscript characters like ₋ⁿ) — always use real LaTeX commands (`\int`, `\sum`, `\infty`, `_{...}`, `^{...}`, `\delta`, `\pi`, etc.) inside `$`/`$$` delimiters instead. Extend this to slide-provided OCR'd formulas too: if the slide text contains a garbled or unicode-notation version of a formula, transcribe it into clean LaTeX rather than reproducing the garbling.
- Do not just restate the slide's bullets in different words — that adds no value over the slide itself.
- The transcript is automatic speech-recognition output and may mis-transcribe technical terms, names, and abbreviations. When a transcript word is clearly a garbled version of a term appearing in the slide text, use the slide's spelling; never carry an obvious ASR artifact into the note.
- Output structured markdown:
  - The slide title as a `##` heading.
  - Key points from the slide as a bullet list.
  - A `**Professor's notes:**` subsection containing only verbal-only content (examples, clarifications, corrections, exam hints, edge cases) that the professor added beyond the slide. Do not put slide-only content in this subsection.
- If the transcript for this slide adds nothing beyond what's already on the slide, do not pad the note — write a single line such as "*No additional commentary beyond the slide.*" instead of a "Professor's notes" subsection.
- Be concise. This is a study aid meant to be read in minutes, not a transcript.
- LANGUAGE: write the entire note in the same language as the lecture content (the slide text and transcript). Never translate it and never mix languages within a note — if the lecture is in Serbian, every sentence you write is in Serbian (LaTeX, standard abbreviations like ADC/SNR, and the literal subsection label "Professor's notes:" are the only exceptions).
- LaTeX hygiene: every `$`/`$$` delimiter must be balanced and the expression inside must be valid, compilable LaTeX. Never nest `$` inside `$$`, never leave a lone `$`, and never mix unicode math symbols into a LaTeX expression."""


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


def build_user_prompt(slide: dict) -> str:
    """Build the per-slide user turn from a segmented_transcripts entry."""
    slide_number = slide.get("slide_number")
    slide_text = (slide.get("slide_text") or "").strip()
    notes_text = (slide.get("notes_text") or "").strip()
    transcript_text = (slide.get("transcript_text") or "").strip()

    return (
        f"Slide number: {slide_number}\n\n"
        f"Slide text (what was on screen):\n{slide_text or '(none)'}\n\n"
        f"Speaker notes (from the deck itself, NOT something the professor said aloud):\n"
        f"{notes_text or '(none)'}\n\n"
        f"Transcript (what the professor said while this slide was on screen):\n"
        f"{transcript_text or '(none)'}"
    )


def generate_slide_note(
    client,
    anthropic_mod,
    model: str,
    slide: dict,
    index: int,
    total: int,
    raw_dir: Path,
) -> dict:
    """Generate notes for a single slide. Returns a result dict; never raises."""
    slide_number = slide.get("slide_number", index)
    slide_text = (slide.get("slide_text") or "").strip()
    transcript_text = (slide.get("transcript_text") or "").strip()

    if not slide_text and not transcript_text:
        print(f"  [{index}/{total}] slide {slide_number}: skipped (no slide text or transcript)")
        return {"skipped": True, "error": False, "slide_number": slide_number}

    user_prompt = build_user_prompt(slide)
    request_payload = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"slide_{slide_number:03d}.json"

    try:
        response = client.messages.create(**request_payload)
    except anthropic_mod.APIError as e:
        print(f"  [{index}/{total}] slide {slide_number}: ERROR: {e}", file=sys.stderr)
        _write_json_atomic(
            raw_path,
            {
                "prompt": request_payload,
                "response": None,
                "model": model,
                "usage": None,
                "error": str(e),
            },
        )
        return {
            "skipped": False,
            "error": True,
            "slide_number": slide_number,
            "message": str(e),
        }

    text_parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    note_text = "\n".join(text_parts).strip()

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }

    _write_json_atomic(
        raw_path,
        {
            "prompt": request_payload,
            "response": note_text,
            "model": model,
            "usage": usage,
        },
    )

    print(
        f"  [{index}/{total}] slide {slide_number}: ok "
        f"(input={usage['input_tokens']} output={usage['output_tokens']} tokens)"
    )

    return {
        "skipped": False,
        "error": False,
        "slide_number": slide_number,
        "text": note_text,
        "usage": usage,
    }


def process_lecture(anthropic_mod, lecture_id: str, force: bool = False) -> bool:
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
    client = anthropic_mod.Anthropic(max_retries=5)

    total = len(slides)
    print(f"[{lecture_id}] generating notes for {total} slide(s) with model={model}")

    md_parts = [f"# {lecture_id}\n"]
    failed_slides = []
    total_input_tokens = 0
    total_output_tokens = 0

    # Per-slide calls are independent, so issue them concurrently — the
    # stage's wall time is pure API latency otherwise. Results are collected
    # by index so note order in the markdown is unaffected. NOTES_CONCURRENCY
    # tunes the pool (lower it if you hit API rate limits; the SDK's
    # max_retries=5 already absorbs occasional 429s).
    from concurrent.futures import ThreadPoolExecutor

    concurrency = max(1, int(os.environ.get("NOTES_CONCURRENCY", "4")))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(generate_slide_note, client, anthropic_mod, model, slide, i, total, raw_dir)
            for i, slide in enumerate(slides, start=1)
        ]
        results = [f.result() for f in futures]  # index-aligned with slides

    # Present slides numbered 1..N in the order they appeared in the lecture.
    # Deck-internal numbers are meaningless to a reader (pooled/merged decks
    # make them huge and non-contiguous); they survive only in the embedded
    # image path and the *_raw/ debug files.
    embed_images = os.environ.get("NOTES_EMBED_IMAGES", "1") != "0"
    images_dir = OUTPUT_NOTES_DIR.parent / "slides_extracted" / f"{lecture_id}_images"

    for position, (slide, result) in enumerate(zip(slides, results), start=1):
        deck_number = slide.get("slide_number", position)

        image_line = ""
        if embed_images:
            image_path = images_dir / f"slide_{deck_number:03d}.png"
            if image_path.exists():
                image_line = (
                    f"\n![slide {position}]"
                    f"(../slides_extracted/{lecture_id}_images/slide_{deck_number:03d}.png)\n"
                )

        if result["skipped"]:
            md_parts.append(f"## Slide {position}\n{image_line}\n*(No slide text or transcript available — skipped.)*\n")
            continue

        if result["error"]:
            failed_slides.append(deck_number)
            md_parts.append(
                f"## Slide {position}\n{image_line}\n"
                f"*(Note generation failed for this slide: {result['message']})*\n"
            )
            continue

        usage = result["usage"]
        total_input_tokens += usage["input_tokens"]
        total_output_tokens += usage["output_tokens"]
        note = add_slide_number_to_heading(result["text"], position)
        if image_line:
            # image goes directly under the slide's heading line
            heading, _, rest = note.partition("\n")
            note = heading + "\n" + image_line + rest
        md_parts.append(note + "\n")

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
        response = client.messages.create(
            model=model,
            max_tokens=8192,  # safety ceiling only — 1024 truncated overviews mid-sentence
            messages=[{"role": "user", "content": summary_prompt}],
        )
        summary_text = "\n".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()
        if summary_text:
            md_parts.insert(1, summary_text + "\n\n---\n")
            print(f"[{lecture_id}] lecture overview generated")
    except anthropic_mod.APIError as e:
        print(f"[{lecture_id}] WARNING: lecture overview failed: {e}", file=sys.stderr)

    if failed_slides:
        md_parts.append(
            "\n---\n\n**Slides that failed to generate:** "
            + ", ".join(str(n) for n in failed_slides)
            + "\n"
        )

    _write_text_atomic(output_path, "\n".join(md_parts))

    status = f" ({len(failed_slides)} slide(s) failed)" if failed_slides else ""
    print(
        f"[done] {lecture_id}: wrote notes -> {output_path}{status} "
        f"(total tokens: input={total_input_tokens} output={total_output_tokens})"
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

    import anthropic  # lazy import — keeps py_compile / --help working without the package installed

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
        ok = process_lecture(anthropic, lecture_id, force=args.force)
        if not ok:
            failed.append(lecture_id)

    if failed:
        print(f"FAILED: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
