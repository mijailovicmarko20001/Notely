"""Worked-example confirmation for stage [6] note generation (opt-in via
NOTES_DETECT_EXAMPLES) -- moved here from scripts/06_generate_notes.py
(Phase 5), split out of notely.pipeline.notes.

Stage 4/5 flag candidate frames with cheap local heuristics (OCR/TF-IDF
score, dHash ink drift, slide-title keyword match); this phase asks a
vision model to confirm each candidate is actually a worked example and
to caption it, before it's embedded in the note markdown. See
EXAMPLE_SYSTEM_PROMPT for the confirmation prompt and CLAUDE.md for the
full feature.

Also holds fmt_ts/frame_md_path/load_frame_image_b64: small helpers this
module and notely.pipeline.notes both need. They live here (not in notes,
not in a third shared module) so the dependency graph stays one-directional
-- notes.py imports from here, this module never imports from notes.py.
"""

import json
import re
import sys
from pathlib import Path

from ..io import save_json
from ..paths import PROJECT_ROOT
from ..ports import LlmApiError

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

# A cheap classify-and-caption task, so it defaults to Haiku rather than
# the notes model.
DEFAULT_EXAMPLES_MODEL = "claude-haiku-4-5"
DEFAULT_EXAMPLES_MAX_PER_LECTURE = 40
EXAMPLE_MAX_TOKENS = 256

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

EXAMPLE_KIND_PRIORITY = {"whiteboard": 0, "annotated_slide": 1, "example_slide": 2}

# Local name kept (rather than updating every call site below) for the
# same atomic-write guarantee, now backed by one shared implementation --
# see notely.io.
_write_json_atomic = save_json


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
    or notes.generate_slide_note), skipping None entries -- a call that
    errored before a response arrived has no usage to add. Same four fields
    both call sites already track, so one summer works for either."""
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
