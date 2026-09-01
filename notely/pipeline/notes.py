"""Stage [6]: note generation (LLM) -- moved here from
scripts/06_generate_notes.py (Phase 5), which is now a thin CLI wrapper
around process_lecture().

For each slide of a lecture, calls the Claude API with the slide's text,
speaker notes, and matched transcript chunk, and asks it to produce
structured per-slide study notes that preserve anything the professor said
that isn't on the slide. Worked-example confirmation (opt-in via
NOTES_DETECT_EXAMPLES) lives in notely.pipeline.examples.
"""

import json
import os
import re
import sys

from ..io import load_json, save_json, write_text_atomic
from ..paths import PROJECT_ROOT
from ..ports import LlmApiError
from .examples import (
    DEFAULT_EXAMPLES_MAX_PER_LECTURE,
    DEFAULT_EXAMPLES_MODEL,
    build_examples_markdown,
    fmt_ts,
    frame_md_path,
    load_frame_image_b64,
    run_example_confirmation,
    sum_usage,
)

INPUT_SEGMENTED_DIR = PROJECT_ROOT / "output" / "segmented_transcripts"
OUTPUT_NOTES_DIR = PROJECT_ROOT / "output" / "notes"

DEFAULT_MODEL = "claude-sonnet-5"
# Safety ceiling only — sized well above any reasonable note so truncation
# never shapes output (brevity comes from the prompt, not the cap).
MAX_TOKENS = 8192

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


VIDEO_SYSTEM_PROMPT = r"""You are generating condensed study notes for one segment of a recorded university lecture that does NOT use a slide deck — a screencast of the instructor working live: writing code, running it, drawing diagrams, working through problems on screen, or talking over what they are doing.

For each segment you are given:
- the segment's position and time range in the lecture
- a transcript of what the instructor said during that segment
- keywords automatically extracted from the text visible on screen during the segment (raw OCR output — noisy, possibly including window titles, browser tabs, or misread characters; treat them as weak hints about the topic, never as quotable content)
- SOMETIMES an image: an actual screen capture from the end of this segment, which is when whatever was being built up (code, a diagram, a result, a derivation) is most complete. If you receive it, it is your best evidence for WHAT was on screen — read it and describe what is actually being demonstrated. Never refer to "the image" or "the screenshot" as a thing in your note; write about the content itself, as if describing what the instructor did.

Your job:
- Capture what a student would need to reconstruct this part of the lecture: the problem being solved, the approach taken, the concrete steps, the result, and any conclusion drawn from it.
- Preserve specifics over generalities. Actual function/library names, actual parameter values, actual numbers, the actual dataset or example being used. "They loaded the data and computed some metrics" is a useless note; "loaded the Lichess chess dataset with pandas, built a directed graph in NetworkX where an edge means player A beat player B, then computed PageRank to rank players" is a useful one.
- Keep definitions and formulas exact. Every formula, without exception, must be proper LaTeX math: `$...$` inline, `$$...$$` on its own line for a standalone equation. Never plain text or unicode math symbols in place of LaTeX commands (no bare "x_a(t)" outside math delimiters, no "∫ from −∞ to +∞", no unicode sub/superscripts) — always real LaTeX (`\int`, `\sum`, `\infty`, `_{...}`, `^{...}`, etc.).
- Code matters here in a way it doesn't for slide lectures. When specific code is written or run on screen, reproduce the meaningful part of it in a fenced code block with the right language tag. Reproduce what was actually written — don't invent plausible-looking code to fill the gap, and don't pad a short snippet out into a full program.
- The transcript is automatic speech-recognition output and will mangle technical terms, library names, and function names. When a transcript word is clearly a garbled version of something visible in the on-screen text or keywords, use the on-screen spelling. Never carry an obvious ASR artifact into the note.
- Output structured markdown:
  - A `##` heading naming what this segment is about — a real topic, not "Segment 3". Derive it from the content.
  - A short bullet list of the key points, steps, or results.
  - A `**Instructor's notes:**` subsection for anything said that isn't evident from the on-screen work itself: motivation, warnings, "this is a common mistake", exam hints, asides, corrections. Omit this subsection entirely if there was nothing of the kind.
- Some segments are genuinely low-content: setup, waiting for something to run, administrative talk, a tangent, technical difficulties. For those the entire note body is one line: "*No substantive content in this segment.*" — do not pad it out. A short note is doing its job.
- Be concise. This is a study aid meant to be read in minutes, not a transcript.
- LANGUAGE: write the entire note in the same language the instructor is speaking (per the transcript). Never translate it and never mix languages within a note — if the lecture is in Serbian, every sentence you write is in Serbian (LaTeX, code, standard abbreviations, and the literal subsection label "Instructor's notes:" are the only exceptions).
- LaTeX hygiene: every `$`/`$$` delimiter must be balanced and the expression inside must be valid, compilable LaTeX. Never nest `$` inside `$$`, never leave a lone `$`."""


def load_dotenv_if_available() -> None:
    """Best-effort .env loading; never fatal if python-dotenv isn't installed."""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass


# Local names kept (rather than updating every call site below) for the
# same atomic-write guarantee, now backed by one shared implementation --
# see notely.io. _write_text_atomic backs the final notes/<id>.md, the one
# file webui/progress.py::artifact_ok checks to decide stage 6 is done and
# skippable on a later run.
_write_json_atomic = save_json
_write_text_atomic = write_text_atomic


def add_slide_number_to_heading(note_text: str, slide_number, label: str = "Slide") -> str:
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
    if re.match(rf"^{re.escape(label)}\s+{re.escape(str(slide_number))}\b", title):
        return note_text  # model already included this exact number

    new_heading = f"## {label} {slide_number}: {title}"
    return new_heading + ("\n" + rest if rest else "")


def build_video_user_prompt(segment: dict, position: int) -> str:
    """Per-segment user turn for visual (deckless) mode -- see
    VIDEO_SYSTEM_PROMPT. Carries the time range and the on-screen keywords
    stage 4's visual segmentation recorded, in place of the slide text a
    deck lecture would have."""
    start = segment.get("start")
    end = segment.get("end")
    keywords = segment.get("segment_keywords") or []
    transcript_text = (segment.get("transcript_text") or "").strip()

    when = f"{fmt_ts(start)}" if start is not None else "?"
    if end is not None:
        when += f" - {fmt_ts(end)}"

    return (
        f"Segment {position} of the recording ({when})\n\n"
        f"Keywords OCR'd from the screen during this segment (noisy hints, not quotable):\n"
        f"{', '.join(keywords) if keywords else '(none)'}\n\n"
        f"Transcript (what the instructor said during this segment):\n"
        f"{transcript_text or '(none)'}"
    )


def build_user_prompt(slide: dict, confirmed_examples: list[dict] | None = None) -> str:
    """Build the per-slide user turn from a segmented_transcripts entry.

    `confirmed_examples`, when non-empty, adds a trailing section listing
    the worked examples stage 6's own confirmation pass found for this
    slide (see notely.pipeline.examples.run_example_confirmation) -- see
    SYSTEM_PROMPT's "Worked examples" clause for how the model is told to
    use it.
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
    raw_dir,
    send_frame_image: bool = False,
    confirmed_examples: list[dict] | None = None,
    visual_mode: bool = False,
) -> dict:
    """Generate notes for a single slide (or, in visual_mode, one on-screen
    segment). Returns a result dict; never raises."""
    slide_number = slide.get("slide_number", index)
    slide_text = (slide.get("slide_text") or "").strip()
    transcript_text = (slide.get("transcript_text") or "").strip()

    # In visual mode there is no slide text by construction, so the transcript
    # alone decides whether there's anything to write about.
    if not transcript_text and (visual_mode or not slide_text):
        what = "segment" if visual_mode else "slide"
        print(f"  [{index}/{total}] {what} {slide_number}: skipped (nothing to summarize)")
        return {"skipped": True, "error": False, "slide_number": slide_number}

    if visual_mode:
        user_prompt = build_video_user_prompt(slide, index)
        system_prompt = VIDEO_SYSTEM_PROMPT
    else:
        user_prompt = build_user_prompt(slide, confirmed_examples)
        system_prompt = SYSTEM_PROMPT

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
        "system": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
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
        slides = load_json(input_path)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[error] {lecture_id}: failed to read {input_path}: {e}", file=sys.stderr)
        return False

    # Stage 5 stamps every entry with the timeline kind it came from (see
    # scripts/05_segment_transcript.py). "visual" means these are on-screen
    # segments of a deckless recording, not deck pages: different note prompt,
    # different heading label, and the captured frame is the only visual there
    # is. Absent means deck mode, so transcripts segmented before visual mode
    # existed still read correctly.
    visual_mode = bool(slides) and slides[0].get("mode") == "visual"
    label = "Segment" if visual_mode else "Slide"

    model = os.environ.get("NOTES_MODEL", DEFAULT_MODEL)
    client = llm_client

    # Opt-in, off by default -- a real added cost (vision tokens on every
    # slide call), not a free win, so this doesn't happen silently. See
    # SYSTEM_PROMPT's image rule and DOCUMENTATION.md for the full feature.
    send_frame_image = os.environ.get("NOTES_SEND_FRAME_IMAGE", "0") == "1"

    # Opt-in, off by default -- same reasoning as NOTES_SEND_FRAME_IMAGE: a
    # real added cost (one vision call per candidate frame), so it doesn't
    # happen silently. See notely.pipeline.examples for the full feature.
    # Worked-example confirmation is deck-relative (stage 4 flags candidates by
    # how *unlike* the deck a frame looks), so it never produces candidates in
    # visual mode -- don't announce a phase that has nothing to do.
    detect_examples = os.environ.get("NOTES_DETECT_EXAMPLES", "0") == "1" and not visual_mode
    examples_model = os.environ.get("NOTES_EXAMPLES_MODEL", DEFAULT_EXAMPLES_MODEL)
    examples_max = max(0, int(os.environ.get("NOTES_EXAMPLES_MAX", str(DEFAULT_EXAMPLES_MAX_PER_LECTURE))))
    examples_cache_path = OUTPUT_NOTES_DIR / f"{lecture_id}_examples.json"

    confirmed_by_slide_number: dict = {}
    examples_usage = sum_usage([])
    if detect_examples:
        if examples_cache_path.exists() and not force:
            cached = load_json(examples_cache_path)
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
                visual_mode=visual_mode,
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
        # No deck render exists in visual mode -- the captured frame below is
        # the only visual this segment has.
        if embed_images and not visual_mode:
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
        # In visual mode the frame is embedded unconditionally: it's the only
        # picture of what this segment was, and embedding an already-captured
        # file costs nothing (NOTES_SEND_FRAME_IMAGE governs paying to send it
        # to the model, which is a separate decision).
        if send_frame_image or visual_mode:
            frame_rel = slide.get("frame_image_path")
            if frame_rel and (PROJECT_ROOT / frame_rel).exists():
                alt = (
                    f"segment {position} on screen"
                    if visual_mode
                    else f"slide {position} as shown during the lecture"
                )
                image_line += f"\n![{alt}]({frame_md_path(frame_rel)})\n"

        examples_md = build_examples_markdown(confirmed_by_slide_number.get(deck_number, []))

        if result["skipped"]:
            md_parts.append(
                f"## {label} {position}\n{image_line}\n*(Nothing to summarize here — skipped.)*\n{examples_md}"
            )
            continue

        if result["error"]:
            failed_slides.append(deck_number)
            md_parts.append(
                f"## {label} {position}\n{image_line}\n"
                f"*(Note generation failed here: {result['message']})*\n{examples_md}"
            )
            continue

        usage = result["usage"]
        total_input_tokens += usage["input_tokens"]
        total_output_tokens += usage["output_tokens"]
        total_cache_write_tokens += usage.get("cache_creation_input_tokens", 0)
        total_cache_read_tokens += usage.get("cache_read_input_tokens", 0)
        if result.get("frame_image_attached"):
            frames_attached += 1
        note = add_slide_number_to_heading(result["text"], position, label)
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
