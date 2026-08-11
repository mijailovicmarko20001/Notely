#!/usr/bin/env python3
"""
Stage [4]: Frame-to-slide matching
=====================================

For each frame-change event detected in stage [3], figure out which slide
number it actually is, using OCR + TF-IDF text similarity constrained by the
assumption that slides are shown roughly in sequential order.

Method (per CLAUDE.md "[4] Frame-to-slide matching"):
  1. OCR each event frame with pytesseract.
  2. Build a per-slide reference text = title + body_text (deliberately
     excluding notes_text, since speaker notes are never actually on
     screen).
  3. Compute TF-IDF cosine similarity between each event's OCR text and
     every slide's reference text.
  4. Apply the sequential-order constraint via a greedy pass (see
     `match_events_to_slides` docstring for the exact rule + tuning knobs).
  5. Collapse consecutive events resolved to the same slide into one
     timeline entry per slide.
  6. Flag anything sketchy into a `_needs_review.json` file: low-confidence
     matches, slides that never got matched, and any backward jump taken.

Usage:
    python scripts/04_match_frames_to_slides.py <lecture_id>
    python scripts/04_match_frames_to_slides.py --all
    python scripts/04_match_frames_to_slides.py <lecture_id> --force
    python scripts/04_match_frames_to_slides.py <lecture_id> --margin 0.2 --confidence-threshold 0.3

Inputs:
    output/frame_events/<lecture_id>.json
        [{"timestamp": float, "frame_image_path": str}, ...]
    output/slides_extracted/<lecture_id>.json
        [{"slide_number": int, "title": str, "body_text": str,
          "notes_text": str, "image_path": str}, ...]

Outputs:
    output/slide_timelines/<lecture_id>.json
        {"timeline": [{"slide_number": int, "start": float,
                        "end": float | null, "confidence": float}, ...],
         "notes": [str, ...]}
    output/slide_timelines/<lecture_id>_needs_review.json
        {"low_confidence_matches": [...], "unmatched_slides": [...],
         "backward_jumps": [...]}

Notes:
    - pytesseract and sklearn are imported lazily inside the functions that
      need them, so `python -m py_compile` and `--help` work without those
      dependencies installed.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Project root = parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Best-effort .env loading (same pattern as stages 00/01/06), so OCR_LANG
# set in .env actually reaches the --ocr-lang default below.
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass
FRAME_EVENTS_DIR = PROJECT_ROOT / "output" / "frame_events"
SLIDES_EXTRACTED_DIR = PROJECT_ROOT / "output" / "slides_extracted"
OUTPUT_DIR = PROJECT_ROOT / "output" / "slide_timelines"
INPUT_VIDEOS_DIR = PROJECT_ROOT / "input" / "videos"

# --- Tuning knobs for the sequential-order constraint (see
# match_events_to_slides for how they're used) ---
DEFAULT_BACKWARD_JUMP_MARGIN = 0.15
DEFAULT_STAY_MARGIN = 0.05
DEFAULT_CONFIDENCE_THRESHOLD = 0.25
DEFAULT_MIN_FORWARD_SCORE = 0.05

OCR_EXCERPT_LEN = 150


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    """Write via a temp file + atomic rename so a killed process (SIGKILL,
    docker stop, host crash) can never leave a truncated-but-non-empty
    artifact — `path.exists() and size > 0` is exactly what
    webui/progress.py::artifact_ok trusts to decide a stage is done and
    skippable on the next run; a partial `open(path, "w")` write would pass
    that check while being invalid JSON, silently corrupting resume."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def ocr_frame(image_path: Path, lang: str = "eng") -> str:
    """OCR a single frame image with pytesseract; returns stripped text ('' on failure)."""
    import pytesseract
    from PIL import Image

    try:
        with Image.open(image_path) as img:
            text = pytesseract.image_to_string(img, lang=lang)
    except Exception as exc:  # noqa: BLE001 - OCR failures shouldn't kill the whole run
        print(f"  warning: OCR failed for {image_path}: {exc}", file=sys.stderr)
        return ""
    return text.strip()


def build_slide_reference_texts(slides: list[dict]) -> dict[int, str]:
    """
    Per-slide reference text = title + body_text.

    notes_text is deliberately excluded: speaker notes were never displayed
    on screen, so they'd only pollute the similarity comparison against
    frames OCR'd off the projected slide.
    """
    refs = {}
    for slide in slides:
        title = slide.get("title", "") or ""
        body = slide.get("body_text", "") or ""
        refs[slide["slide_number"]] = f"{title}\n{body}".strip()
    return refs


def compute_similarity_matrix(event_texts: list[str], slide_texts: list[str]):
    """
    TF-IDF cosine similarity between each event's OCR text and each slide's
    reference text. Returns an (n_events x n_slides) numpy array.

    Fit the vectorizer jointly over events + slide texts so both sides share
    a vocabulary. If every text is empty (e.g. OCR totally failed / no
    slides), returns an all-zero matrix rather than raising.
    """
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    n_events, n_slides = len(event_texts), len(slide_texts)
    if n_events == 0 or n_slides == 0:
        return np.zeros((n_events, n_slides))

    corpus = event_texts + slide_texts
    if not any(t.strip() for t in corpus):
        return np.zeros((n_events, n_slides))

    # ngram_range=(1, 2) helps absorb minor OCR word-splitting noise;
    # stop_words filters filler words common to both OCR text and slide
    # prose that would otherwise dominate the similarity score.
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
    try:
        tfidf = vectorizer.fit_transform(corpus)
    except ValueError:
        # Empty vocabulary after stop-word removal (e.g. all-numeric/blank OCR).
        return np.zeros((n_events, n_slides))

    event_vecs = tfidf[:n_events]
    slide_vecs = tfidf[n_events:]
    return cosine_similarity(event_vecs, slide_vecs)


def match_events_to_slides(
    events: list[dict],
    slide_numbers: list[int],
    sim_matrix,
    margin: float,
    stay_margin: float = DEFAULT_STAY_MARGIN,
    min_forward_score: float = DEFAULT_MIN_FORWARD_SCORE,
) -> list[dict]:
    """
    Assign each event to a slide number using a greedy pass constrained by
    the sequential-order assumption.

    Chosen rule (greedy, not full Viterbi/DP): at each event, compare the
    best-scoring slide among those that keep the sequence non-decreasing
    (slide_number >= current_slide) against the best-scoring slide among
    "backward" candidates (slide_number < current_slide). Take the backward
    candidate ONLY if its score beats the in-order best by more than
    `margin`; otherwise take the in-order best, even if its score is low
    (low scores are instead surfaced via the confidence threshold in
    needs_review, not silently overridden by a jump).

    A greedy pass was chosen over a full DP/Viterbi because it's simpler to
    reason about and log at each step (this is the highest-risk stage in
    the pipeline per CLAUDE.md, so a human being able to read "event 12:
    stayed in order at slide 4 (score 0.31) vs backward candidate slide 2
    (score 0.38, margin not met)" from the code path matters more here than
    squeezing out a globally-optimal assignment).

    Tuning knobs:
      - margin (DEFAULT_BACKWARD_JUMP_MARGIN): how much stronger a backward
        match must be, in absolute cosine-similarity, before we trust a
        cursor-back / re-explained-slide jump over just staying in order.
        Raise this if false "jump backs" are happening on noisy OCR; lower
        it if genuine revisits are being missed.
      - stay_margin (DEFAULT_STAY_MARGIN): how much a competing in-order
        candidate must beat the *current* slide's own score before we leave
        the current slide at all. Without this, decks with several
        consecutive near-duplicate slides (e.g. a multi-slide formula
        derivation that repeats the same header/body across slides) can
        cause the cursor to bounce between two or three of them event to
        event, since their scores are within noise of each other -- this
        showed up in practice as the same slide title appearing many times
        in generated notes. Raise this if genuine slide advances are being
        suppressed; lower it (toward 0) to fall back to the old
        always-take-the-max behavior.
      - forward jumps (skipping slide numbers, e.g. 3 -> 6) are otherwise
        allowed with no further penalty beyond stay_margin, since "professor
        advances past a slide quickly" is common and undetectable from
        timing alone. stay_margin alone doesn't fully guard against a
        nonsense jump, though: if the current slide's own score has also
        decayed near zero (OCR noise, or the professor lingered past the
        point the frame still resembles that slide), a forward candidate
        that's *also* near zero can still beat it by stay_margin and win "by
        default" as the nominal max even though neither score means
        anything.
      - min_forward_score (DEFAULT_MIN_FORWARD_SCORE): absolute floor an
        in-order candidate's score must clear before it's allowed to move
        the cursor off the current slide. Guards against the near-zero-vs-
        near-zero case above. Set to 0 to fall back to the old
        stay_margin-only behavior.
        Deliberately conservative (0.05), and this is a real constraint,
        not a tuning nicety: on lecture01's actual data, the one *known*
        residual spurious match (a jump to "slide 71" following a blank/
        failed-OCR frame) scores 0.15 — but so do several genuine
        transitions in the same lecture (0.16-0.21, including a backward
        jump the manual review explicitly confirmed as correct). There is
        no score value that separates that spurious match from real ones
        without also rejecting real ones; a global floor can only catch
        truly-near-zero nonsense, not this specific case. Verified this
        floor makes zero difference to lecture01's full match sequence at
        its default value — it's a forward-looking guard against a worse
        version of the same failure mode on other lectures, not a fix for
        the known lecture01 case (which stays as its existing manual
        correction in the timeline's own notes field).

    Returns a list of per-event match dicts:
        {event_index, timestamp, frame_image_path, ocr_excerpt,
         slide_number, score, jumped_backward, stayed_over_raw_best,
         in_order_best_slide, in_order_best_score, backward_best_slide,
         backward_best_score}
    """
    if not slide_numbers:
        return []

    sorted_slides = sorted(slide_numbers)
    current_slide = sorted_slides[0]
    matches = []

    for i, event in enumerate(events):
        scores = {slide_num: sim_matrix[i][j] for j, slide_num in enumerate(sorted_slides)}

        in_order_candidates = {s: sc for s, sc in scores.items() if s >= current_slide}
        backward_candidates = {s: sc for s, sc in scores.items() if s < current_slide}

        raw_best_slide, raw_best_score = max(in_order_candidates.items(), key=lambda kv: kv[1])

        # Stickiness: don't leave the current slide unless some other
        # in-order candidate clearly beats just staying put AND clears an
        # absolute floor (min_forward_score) — otherwise a near-zero
        # candidate can "win" by stay_margin alone when the current slide's
        # own score has also decayed near zero, which is noise, not signal.
        current_score = scores[current_slide]
        stayed_over_raw_best = raw_best_slide != current_slide and (
            raw_best_score <= current_score + stay_margin or raw_best_score < min_forward_score
        )
        if stayed_over_raw_best:
            in_order_best_slide, in_order_best_score = current_slide, current_score
        else:
            in_order_best_slide, in_order_best_score = raw_best_slide, raw_best_score

        backward_best_slide, backward_best_score = (None, None)
        if backward_candidates:
            backward_best_slide, backward_best_score = max(
                backward_candidates.items(), key=lambda kv: kv[1]
            )

        jumped_backward = (
            backward_best_slide is not None
            and backward_best_score > in_order_best_score + margin
        )

        if jumped_backward:
            chosen_slide, chosen_score = backward_best_slide, backward_best_score
        else:
            chosen_slide, chosen_score = in_order_best_slide, in_order_best_score

        current_slide = chosen_slide

        matches.append(
            {
                "event_index": i,
                "timestamp": event["timestamp"],
                "frame_image_path": event["frame_image_path"],
                "ocr_excerpt": event.get("ocr_text", "")[:OCR_EXCERPT_LEN],
                "slide_number": chosen_slide,
                "score": round(float(chosen_score), 4),
                "jumped_backward": jumped_backward,
                "stayed_over_raw_best": stayed_over_raw_best,
                "in_order_best_slide": in_order_best_slide,
                "in_order_best_score": round(float(in_order_best_score), 4),
                "backward_best_slide": backward_best_slide,
                "backward_best_score": (
                    round(float(backward_best_score), 4) if backward_best_score is not None else None
                ),
            }
        )

    return matches


def get_video_duration(lecture_id: str) -> float | None:
    """Best-effort video duration in seconds via ffprobe; None if unavailable."""
    video_path = INPUT_VIDEOS_DIR / f"{lecture_id}.mp4"
    if not video_path.exists():
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.decode().strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def collapse_to_timeline(matches: list[dict], video_duration: float | None) -> tuple[list[dict], list[str]]:
    """
    Collapse consecutive events matched to the same slide into single
    timeline entries. End time of a slide = start timestamp of the next
    (different-slide) event. Confidence of a collapsed entry = max score
    across the events collapsed into it (best evidence for that slide).

    Last slide's end: video duration via ffprobe if the source video is
    available, else falls back to the last event's own timestamp (with a
    note explaining the fallback was used), else null.
    """
    notes = []
    if not matches:
        return [], notes

    runs = []  # list of lists of matches, grouped by consecutive same slide_number
    for m in matches:
        if runs and runs[-1][-1]["slide_number"] == m["slide_number"]:
            runs[-1].append(m)
        else:
            runs.append([m])

    timeline = []
    for idx, run in enumerate(runs):
        slide_number = run[0]["slide_number"]
        start = run[0]["timestamp"]
        confidence = max(m["score"] for m in run)

        if idx + 1 < len(runs):
            end = runs[idx + 1][0]["timestamp"]
        else:
            if video_duration is not None:
                end = video_duration
            else:
                end = run[-1]["timestamp"]
                notes.append(
                    f"last slide (slide_number={slide_number}) end uses the last matched "
                    "event's timestamp, not true video end, because ffprobe/video was "
                    "unavailable to determine actual video duration"
                )
                if end == start:
                    notes.append(
                        f"last slide (slide_number={slide_number}) end == start "
                        "(zero-width) because it was matched by only one event and true "
                        "video end is unknown"
                    )

        timeline.append(
            {
                "slide_number": slide_number,
                "start": start,
                "end": end,
                "confidence": round(float(confidence), 4),
            }
        )

    return timeline, notes


def build_needs_review(
    matches: list[dict],
    timeline: list[dict],
    slides: list[dict],
    confidence_threshold: float,
) -> dict:
    low_confidence_matches = [
        {
            "event_index": m["event_index"],
            "timestamp": m["timestamp"],
            "frame_image_path": m["frame_image_path"],
            "ocr_excerpt": m["ocr_excerpt"],
            "slide_number": m["slide_number"],
            "score": m["score"],
        }
        for m in matches
        if m["score"] < confidence_threshold
    ]

    matched_slide_numbers = {entry["slide_number"] for entry in timeline}
    unmatched_slides = [
        {"slide_number": s["slide_number"], "title": s.get("title", "")}
        for s in slides
        if s["slide_number"] not in matched_slide_numbers
    ]

    backward_jumps = [
        {
            "event_index": m["event_index"],
            "timestamp": m["timestamp"],
            "frame_image_path": m["frame_image_path"],
            "ocr_excerpt": m["ocr_excerpt"],
            "jumped_to_slide": m["slide_number"],
            "jumped_to_score": m["score"],
            "in_order_best_slide": m["in_order_best_slide"],
            "in_order_best_score": m["in_order_best_score"],
        }
        for m in matches
        if m["jumped_backward"]
    ]

    return {
        "low_confidence_matches": low_confidence_matches,
        "unmatched_slides": unmatched_slides,
        "backward_jumps": backward_jumps,
    }


def process_lecture(
    lecture_id: str,
    margin: float,
    confidence_threshold: float,
    force: bool,
    ocr_lang: str = "eng",
    stay_margin: float = DEFAULT_STAY_MARGIN,
    min_forward_score: float = DEFAULT_MIN_FORWARD_SCORE,
) -> None:
    events_path = FRAME_EVENTS_DIR / f"{lecture_id}.json"
    slides_path = SLIDES_EXTRACTED_DIR / f"{lecture_id}.json"
    output_json = OUTPUT_DIR / f"{lecture_id}.json"
    needs_review_json = OUTPUT_DIR / f"{lecture_id}_needs_review.json"

    if not events_path.exists():
        print(f"[skip] {lecture_id}: no frame events found at {events_path}", file=sys.stderr)
        return
    if not slides_path.exists():
        print(f"[skip] {lecture_id}: no extracted slides found at {slides_path}", file=sys.stderr)
        return

    if output_json.exists() and not force:
        print(f"[skip] {lecture_id}: {output_json} already exists (use --force to redo)")
        return

    events = load_json(events_path)
    slides = load_json(slides_path)

    if not events:
        print(f"[{lecture_id}] no frame events to match, writing empty timeline")
        timeline, notes = [], ["no frame events were available to match"]
        matches = []
    else:
        print(f"[{lecture_id}] OCR'ing {len(events)} event frames (lang={ocr_lang})...")
        for i, event in enumerate(events):
            frame_path = PROJECT_ROOT / event["frame_image_path"]
            # per-frame progress marker — OCR is this stage's long pole
            print(f"  [ocr {i + 1}/{len(events)}] {frame_path.name}", flush=True)
            event["ocr_text"] = ocr_frame(frame_path, lang=ocr_lang)

        slide_numbers = sorted(s["slide_number"] for s in slides)
        slide_refs = build_slide_reference_texts(slides)
        event_texts = [e["ocr_text"] for e in events]
        slide_texts = [slide_refs[n] for n in slide_numbers]

        print(f"[{lecture_id}] computing TF-IDF cosine similarity ({len(events)} events x {len(slide_numbers)} slides)...")
        sim_matrix = compute_similarity_matrix(event_texts, slide_texts)

        print(
            f"[{lecture_id}] matching events to slides "
            f"(margin={margin}, stay_margin={stay_margin}, min_forward_score={min_forward_score})..."
        )
        matches = match_events_to_slides(
            events, slide_numbers, sim_matrix, margin, stay_margin, min_forward_score
        )
        for m in matches:
            jump_tag = " [BACKWARD JUMP]" if m["jumped_backward"] else ""
            stay_tag = " [STAYED]" if m["stayed_over_raw_best"] else ""
            print(
                f"  event {m['event_index']:03d} t={m['timestamp']:8.2f}s -> "
                f"slide {m['slide_number']} (score={m['score']:.3f}){jump_tag}{stay_tag}"
            )

        video_duration = get_video_duration(lecture_id)
        timeline, notes = collapse_to_timeline(matches, video_duration)

    needs_review = build_needs_review(matches, timeline, slides, confidence_threshold)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    save_json(output_json, {"timeline": timeline, "notes": notes})
    save_json(needs_review_json, needs_review)

    n_flags = (
        len(needs_review["low_confidence_matches"])
        + len(needs_review["unmatched_slides"])
        + len(needs_review["backward_jumps"])
    )
    print(
        f"[done] {lecture_id}: {len(timeline)} slide(s) in timeline -> {output_json} "
        f"({n_flags} item(s) flagged -> {needs_review_json})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage [4]: match frame-change events to slide numbers via OCR + TF-IDF."
    )
    parser.add_argument(
        "lecture_id",
        nargs="?",
        default=None,
        help="Lecture id, e.g. lecture01 (matches output/frame_events/<lecture_id>.json)",
    )
    parser.add_argument("--all", action="store_true", help="process every lecture found in output/frame_events/")
    parser.add_argument(
        "--force", action="store_true", help="re-run even if output/slide_timelines/<lecture_id>.json exists"
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=DEFAULT_BACKWARD_JUMP_MARGIN,
        help=f"similarity margin required to accept a backward jump (default: {DEFAULT_BACKWARD_JUMP_MARGIN})",
    )
    parser.add_argument(
        "--stay-margin",
        type=float,
        default=DEFAULT_STAY_MARGIN,
        help=(
            "similarity margin a competing in-order candidate must beat the current "
            f"slide's own score by before the cursor advances (default: {DEFAULT_STAY_MARGIN}). "
            "Curbs oscillation between near-duplicate consecutive slides."
        ),
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        help=f"matches below this score are flagged in needs_review.json (default: {DEFAULT_CONFIDENCE_THRESHOLD})",
    )
    parser.add_argument(
        "--min-forward-score",
        type=float,
        default=DEFAULT_MIN_FORWARD_SCORE,
        help=(
            "absolute score floor an in-order candidate must clear to move the cursor "
            f"off the current slide (default: {DEFAULT_MIN_FORWARD_SCORE}). Set to 0 for "
            "the old stay_margin-only behavior."
        ),
    )
    parser.add_argument(
        "--ocr-lang",
        default=os.environ.get("OCR_LANG", "eng"),
        help='tesseract language(s), e.g. "srp_latn+eng" (default: env OCR_LANG or "eng")',
    )
    args = parser.parse_args()

    if bool(args.all) == bool(args.lecture_id):
        parser.error("provide exactly one of <lecture_id> or --all")

    if args.all:
        event_files = sorted(FRAME_EVENTS_DIR.glob("*.json"))
        # exclude nothing special here since frame_events only ever contains
        # <lecture_id>.json files (frame PNGs live in a sibling _frames/ dir)
        lecture_ids = [p.stem for p in event_files]
        if not lecture_ids:
            print(f"No frame event files found in {FRAME_EVENTS_DIR}")
            return
    else:
        lecture_ids = [args.lecture_id]

    for lecture_id in lecture_ids:
        process_lecture(
            lecture_id,
            margin=args.margin,
            confidence_threshold=args.confidence_threshold,
            force=args.force,
            ocr_lang=args.ocr_lang,
            stay_margin=args.stay_margin,
            min_forward_score=args.min_forward_score,
        )


if __name__ == "__main__":
    main()
