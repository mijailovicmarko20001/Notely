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
    output/slide_timelines/<lecture_id>_examples.json
        {"candidates": [{"event_index": int, "timestamp": float,
                          "frame_image_path": str, "slide_number": int,
                          "kind": "whiteboard"|"annotated_slide"|"example_slide",
                          "score": float, "ink_delta": int | null,
                          "ocr_excerpt": str}, ...]}
        Frame events that look like the professor working a worked example --
        off-deck (whiteboard/tablet), annotated live over a slide, or the
        deck's own example slide. Always computed (free, local heuristics);
        confirmation/captioning by an LLM happens downstream in stage 6, gated
        behind NOTES_DETECT_EXAMPLES. See CLAUDE.md for the full feature.

Notes:
    - pytesseract and sklearn are imported lazily inside the functions that
      need them, so `python -m py_compile` and `--help` work without those
      dependencies installed.
"""

import argparse
import os
import re
import sys
import unicodedata
from pathlib import Path

# Only needed to bootstrap the `from notely...` import below (finding
# notely/ on sys.path) -- notely.paths.PROJECT_ROOT is the same value and
# is what the rest of this file uses.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent

# notely/ (ports, adapters, paths) lives alongside scripts/ and webui/ at
# the project root, not on sys.path by default when this file is run
# directly (python scripts/04_match_frames_to_slides.py) -- same fix
# tests/conftest.py applies for test discovery. Must happen before the
# `from notely...` import below.
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.adapters.ffprobe_media_probe import FfprobeMediaProbe  # noqa: E402
from notely.adapters.tesseract_ocr import TesseractOcr  # noqa: E402
from notely.io import load_json, save_json  # noqa: E402
from notely.paths import PROJECT_ROOT  # noqa: E402
from notely.paths import VIDEOS_DIR as INPUT_VIDEOS_DIR  # noqa: E402

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

# --- Tuning knobs for the sequential-order constraint (see
# match_events_to_slides for how they're used) ---
DEFAULT_BACKWARD_JUMP_MARGIN = 0.15
DEFAULT_STAY_MARGIN = 0.05
DEFAULT_CONFIDENCE_THRESHOLD = 0.25
DEFAULT_MIN_FORWARD_SCORE = 0.05

OCR_EXCERPT_LEN = 150

# --- Tuning knobs for worked-example frame detection (see
# detect_example_candidates for how they're used) ---
DEFAULT_EXAMPLE_SCORE_MAX = 0.12  # below this, a frame doesn't resemble the deck at all
DEFAULT_EXAMPLE_INK_DELTA = 12  # dHash Hamming distance (of 64 bits) from a run's first frame
# Minimum fraction of the run's first frame's OCR words that must still be
# present in a candidate frame's OCR text before dHash drift is trusted as
# "ink added on top of the same slide" (annotated_slide) rather than "this
# is actually a different slide that the sequential-order matcher failed to
# split into its own run" -- see detect_example_candidates for why this
# guard exists (found on real pooled-deck footage: several genuinely
# different, unrelated slides sharing one run, dHash drift alone couldn't
# tell them apart from real annotation).
DEFAULT_EXAMPLE_INK_TEXT_OVERLAP_MIN = 0.5
# Minimum fraction of a candidate frame's own OCR words that must be ABSENT
# from the deck's own extracted text (stage 2's exact source text for the
# matched slide_number, not an OCR guess) before dHash drift + OCR overlap
# together count as real ink. This is the ground-truth check the other two
# can't provide on their own: a slide whose content is revealed progressively
# by a PowerPoint animation build still has every one of its eventual words
# in the deck's own (full, final) extracted text, so a mid-build frame has
# ~0 "novel" words even though it visually differs a lot from the run's
# first frame and even from the deck's own rendered image at that instant --
# it just hasn't been fully built out yet, nothing was added beyond print.
# Real handwritten/drawn ink, by contrast, is never in the deck's source
# text at all, so it always shows up as novel words here. Calibrated
# against real false positives found on this course's footage: a dense
# multi-box slide mid-build measured at exactly 0.0 novel, comfortably
# separated from this default -- but two more frames of a second, heavily
# diacritic-bearing slide (Tesseract misreads č/ć as visually-similar
# unrelated letters like é, which _fold_diacritics can't repair since it's
# a wrong base glyph, not just a missing accent mark) measured at 0.17 and
# exactly 0.2, both still zero-annotation false positives. That cluster is
# far too close to a naive 0.2 to leave any real margin against ordinary
# OCR noise on this kind of text -- 0.35 clears both with room to spare,
# while a genuine worked example (an actual derivation, formula, or
# calculation) should introduce far more novel content than a handful of
# misread words.
DEFAULT_EXAMPLE_INK_NOVEL_WORD_MIN = 0.35
EXAMPLE_SLIDE_RE = re.compile(r"primer|zadatak|vežb|vezb|example|exercise", re.IGNORECASE)


DHASH_SIZE = 8
# Hamming distance (of 64 bits) below which two consecutive event frames are
# treated as near-duplicates and skip a second OCR call. Chosen from real
# data, not guessed: across all 22 lectures in this project's course, the
# smallest distance between two frames stage 3 judged genuinely *different*
# was well above this; several lectures also have real near-duplicate
# consecutive events (distance 0-3 -- e.g. a cursor-triggered false slide-
# change event, or an animation frame) that this threshold safely catches
# without risking a false merge of two visually similar-but-different slides
# (e.g. a multi-slide formula derivation).
DHASH_DEDUP_THRESHOLD = 3


def frame_hash(image_path: Path, hash_size: int = DHASH_SIZE) -> int:
    """Difference hash (dHash) of a frame image: resize to (n+1)xn grayscale,
    then one bit per pixel for whether it's darker than its right neighbor.
    Cheap (~ms) and dependency-free (PIL only, already required for OCR) way
    to catch near-identical consecutive frames before spending an OCR call
    on both -- OCR is this stage's own long pole (see the per-frame progress
    marker below)."""
    from PIL import Image

    with Image.open(image_path) as img:
        img = img.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
        pixels = list(img.getdata())
    bits = 0
    for row in range(hash_size):
        row_start = row * (hash_size + 1)
        for col in range(hash_size):
            bits = (bits << 1) | int(pixels[row_start + col] > pixels[row_start + col + 1])
    return bits


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


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
            backward_best_slide, backward_best_score = max(backward_candidates.items(), key=lambda kv: kv[1])

        jumped_backward = (
            backward_best_slide is not None and backward_best_score > in_order_best_score + margin
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


def group_runs(matches: list[dict]) -> list[list[dict]]:
    """Group consecutive matches with the same slide_number into runs.

    Factored out of collapse_to_timeline so example detection
    (detect_example_candidates) groups events identically to the timeline
    it augments -- the two must never drift apart, or a candidate's
    "run" could disagree with the timeline entry it's meant to attach to.
    """
    runs = []
    for m in matches:
        if runs and runs[-1][-1]["slide_number"] == m["slide_number"]:
            runs[-1].append(m)
        else:
            runs.append([m])
    return runs


def collapse_to_timeline(matches: list[dict], video_duration: float | None) -> tuple[list[dict], list[str]]:
    """
    Collapse consecutive events matched to the same slide into single
    timeline entries. End time of a slide = start timestamp of the next
    (different-slide) event. Confidence of a collapsed entry = max score
    across the events collapsed into it (best evidence for that slide).

    Last slide's end: video duration via ffprobe if the source video is
    available, else falls back to the last event's own timestamp (with a
    note explaining the fallback was used), else null.

    Also keeps the LAST event's frame_image_path per run as
    `last_frame_image_path` -- the actual on-screen capture of that slide
    right before the professor moved on, as opposed to the clean deck
    render stage 2 produces. Live annotations (writing/drawing on the
    slide) accumulate over the run's dwell time, so the last frame is the
    most complete one. Used downstream (stage 6, opt-in via
    NOTES_SEND_FRAME_IMAGE) to let note generation see -- and let a human
    reader see -- what was actually on screen, not just the printed deck.
    """
    notes = []
    if not matches:
        return [], notes

    runs = group_runs(matches)

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
                "last_frame_image_path": run[-1]["frame_image_path"],
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


def _fold_diacritics(text: str) -> str:
    """NFKD-decompose and drop combining marks, so an accented and
    unaccented spelling of the same word compare equal. Serbian-latin OCR
    output is inconsistent about diacritics (Tesseract frequently drops or
    misreads č/ć/š/ž/đ at video resolution) while stage 2's deck-extracted
    text always has them, so comparing the two without folding manufactures
    spurious "different word" mismatches on otherwise-identical text. Same
    technique as stage 5's fold(), duplicated rather than imported since
    each stage script is meant to run standalone."""
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in normalized if not unicodedata.combining(c))


def _ocr_words(text: str) -> list[str]:
    """Lowercased, diacritic-folded, punctuation-stripped words, in order."""
    return re.findall(r"[\w']+", _fold_diacritics(text or "").lower())


def ocr_text_overlap(reference: str, candidate: str) -> float:
    """
    Word-level containment ratio: the fraction of `reference`'s words that
    also appear in `candidate` (see _ocr_words for normalization).

    Directional, not symmetric: used to check whether a later frame's OCR
    text still contains most of an earlier frame's printed text. Ink
    (handwriting/drawing) laid on top of a slide can only add pixels dHash
    reacts to -- it doesn't erase the slide's own printed text underneath,
    so real annotation keeps this ratio high. A frame that's actually a
    *different* slide (e.g. the sequential-order matcher failed to split it
    into its own run) usually shares only stopwords/numbers with the
    original, so this ratio drops.

    Returns 1.0 if `reference` has no words at all -- there's nothing to
    check containment of (e.g. a diagram-only slide with no OCR'd text), so
    the caller should treat that as "can't rule it out" rather than "no
    overlap, reject".
    """
    reference_words = set(_ocr_words(reference))
    if not reference_words:
        return 1.0
    candidate_words = set(_ocr_words(candidate))
    return len(reference_words & candidate_words) / len(reference_words)


def ocr_novel_word_ratio(deck_text: str, frame_ocr: str) -> float:
    """
    Fraction of `frame_ocr`'s words (see _ocr_words for normalization) that
    do NOT appear anywhere in `deck_text` -- the deck's own extracted
    source text for the matched slide (stage 2's exact text, not an OCR
    guess), which already contains everything the slide will ever show
    across every step of a PowerPoint animation build, not just what's
    visible at this instant.

    This is the ground-truth check ocr_text_overlap (against the run's
    first *captured* frame) can't provide on its own: a mid-build frame of
    a busy slide differs a lot from the run's first frame in pixels, but
    every one of its words is still something the deck itself prints
    somewhere -- zero novel words. Real handwritten/drawn ink is never in
    the deck's source text at all, so it always registers as novel words.

    `frame_ocr`'s trailing word is dropped before comparing whenever the
    raw text is at (or near) OCR_EXCERPT_LEN -- match_events_to_slides
    truncates OCR text to that length, so a candidate at the boundary very
    likely had its last word cut off mid-word rather than ending where the
    professor's slide actually did, which would otherwise register as a
    spurious "novel" word on every single long excerpt (observed on real
    footage: "proizvoljnim" truncated to "proizv", not in the deck's own
    text as a fragment even though the full word is).

    Returns 1.0 if `frame_ocr` has no words at all -- OCR found no printed
    text to compare (e.g. Tesseract failed on it, or the added content is a
    hand-drawn diagram/circuit with no recognizable words), so this check
    can't rule out real annotation; treat it as "can't tell" rather than
    "no evidence of novelty, reject" and defer to the other two checks
    (ink_delta, ocr_text_overlap) instead.
    """
    frame_words = _ocr_words(frame_ocr)
    if not frame_words:
        return 1.0
    if len(frame_ocr or "") >= OCR_EXCERPT_LEN - 1 and len(frame_words) > 1:
        frame_words = frame_words[:-1]
    frame_word_set = set(frame_words)
    deck_words = set(_ocr_words(deck_text))
    return len(frame_word_set - deck_words) / len(frame_word_set)


def detect_example_candidates(
    matches: list[dict],
    slides: list[dict],
    hashes: dict[int, int],
    score_max: float = DEFAULT_EXAMPLE_SCORE_MAX,
    ink_delta: int = DEFAULT_EXAMPLE_INK_DELTA,
    ink_text_overlap_min: float = DEFAULT_EXAMPLE_INK_TEXT_OVERLAP_MIN,
    ink_novel_word_min: float = DEFAULT_EXAMPLE_INK_NOVEL_WORD_MIN,
) -> list[dict]:
    """
    Flag frame events that likely show the professor working a concrete
    example -- on a whiteboard/tablet away from the deck, annotated live
    over a slide, or on the deck's own dedicated example slide -- so stage
    6 can offer them to an LLM for confirmation and captioning.

    Classifies each event in a run (see group_runs) with the first rule
    that fires:
      - "whiteboard": the OCR/TF-IDF match score is too low for this frame
        to be the printed deck at all -- the screen switched to a
        whiteboard, doc-cam, or scratch page.
      - "annotated_slide": all three of the following, each catching a
        different way pixels-only drift produces a false positive:
          1. the frame's dHash has drifted far enough from the run's first
             frame (ink_delta) -- encoder/compression noise stays well
             under this; see DHASH_DEDUP_THRESHOLD for the equivalent
             "these are the same frame" floor at the other end of the
             scale.
          2. this frame's OCR text still contains most of the run's first
             frame's OCR text (ocr_text_overlap >= ink_text_overlap_min) --
             rules out the sequential-order matcher having silently left
             the cursor on one slide_number while the visual content
             actually changed underneath to an unrelated slide (observed on
             real pooled-deck footage).
          3. this frame's OCR text contains words the deck's OWN extracted
             text for that slide (stage 2's exact source text, not an OCR
             guess) never prints anywhere (ocr_novel_word_ratio >=
             ink_novel_word_min) -- rules out a slide whose content is
             revealed progressively by a PowerPoint animation build: every
             build step's words are already in the deck's full extracted
             text, so a mid-build frame has ~0 novel words despite
             differing a lot, pixel-wise, from the run's first frame (also
             observed on real footage: a dense multi-box slide mid-build,
             measured at exactly 0.0 novel words). Real ink is never in the
             deck's source text at all, so it always registers as novel.
      - "example_slide": the matched slide's own title/body text names it
        as a worked example (EXAMPLE_SLIDE_RE), independent of score or
        ink drift -- catches a deck's own "Primer 3" slide even when
        nothing was annotated on it.

    Every other event is not a candidate. Consecutive same-kind candidates
    within a run are then collapsed to the LAST one, on the same reasoning
    as collapse_to_timeline's last_frame_image_path: ink/writing accumulates
    over the run's dwell time, so the last frame is the most complete
    capture, and collapsing keeps the stage-6 API call count proportional
    to distinct examples rather than sampled frames.

    `hashes` maps event_index -> dHash (see frame_hash), covering every
    event regardless of whether its OCR was deduped against a neighbor --
    ink drift must be measured frame-to-frame, not OCR-call-to-OCR-call.
    """
    if not matches:
        return []

    slide_texts = {
        s["slide_number"]: f"{s.get('title', '') or ''}\n{s.get('body_text', '') or ''}" for s in slides
    }

    candidates = []
    for run in group_runs(matches):
        run_first_hash = hashes.get(run[0]["event_index"])
        run_first_ocr = run[0].get("ocr_excerpt", "")
        run_candidates = []

        for m in run:
            idx = m["event_index"]
            h = hashes.get(idx)
            ink_delta_value = (
                hamming_distance(h, run_first_hash) if h is not None and run_first_hash is not None else None
            )
            deck_text = slide_texts.get(m["slide_number"], "")
            frame_ocr = m.get("ocr_excerpt", "")

            if m["score"] < score_max:
                kind = "whiteboard"
            elif (
                ink_delta_value is not None
                and ink_delta_value >= ink_delta
                and ocr_text_overlap(run_first_ocr, frame_ocr) >= ink_text_overlap_min
                and ocr_novel_word_ratio(deck_text, frame_ocr) >= ink_novel_word_min
            ):
                kind = "annotated_slide"
            elif EXAMPLE_SLIDE_RE.search(deck_text):
                kind = "example_slide"
            else:
                continue

            run_candidates.append(
                {
                    "event_index": idx,
                    "timestamp": m["timestamp"],
                    "frame_image_path": m["frame_image_path"],
                    "slide_number": m["slide_number"],
                    "kind": kind,
                    "score": m["score"],
                    "ink_delta": ink_delta_value,
                    "ocr_excerpt": m["ocr_excerpt"],
                }
            )

        # Collapse consecutive same-kind candidates within this run, keeping
        # the last (most complete, most-annotated) one.
        collapsed = []
        for c in run_candidates:
            if collapsed and collapsed[-1]["kind"] == c["kind"]:
                collapsed[-1] = c
            else:
                collapsed.append(c)
        candidates.extend(collapsed)

    return candidates


def process_lecture(
    lecture_id: str,
    margin: float,
    confidence_threshold: float,
    force: bool,
    ocr_lang: str = "eng",
    ocr=None,
    media_probe=None,
    stay_margin: float = DEFAULT_STAY_MARGIN,
    min_forward_score: float = DEFAULT_MIN_FORWARD_SCORE,
    detect_examples: bool = True,
    example_score_max: float = DEFAULT_EXAMPLE_SCORE_MAX,
    example_ink_delta: int = DEFAULT_EXAMPLE_INK_DELTA,
    example_ink_text_overlap_min: float = DEFAULT_EXAMPLE_INK_TEXT_OVERLAP_MIN,
    example_ink_novel_word_min: float = DEFAULT_EXAMPLE_INK_NOVEL_WORD_MIN,
) -> bool:
    """Returns False only when required input (frame events or extracted
    slides) was missing (the caller should treat that as a failure); an
    already-done skip and a real successful run both return True.

    ocr: an Ocr (see notely.ports), defaults to the real pytesseract-backed
    adapter. media_probe: a MediaProbe, defaults to the real ffprobe-backed
    adapter. Both default to their real adapter; tests inject a fake
    instead of needing tesseract/ffprobe installed."""
    if ocr is None:
        ocr = TesseractOcr()
    if media_probe is None:
        media_probe = FfprobeMediaProbe()
    events_path = FRAME_EVENTS_DIR / f"{lecture_id}.json"
    slides_path = SLIDES_EXTRACTED_DIR / f"{lecture_id}.json"
    output_json = OUTPUT_DIR / f"{lecture_id}.json"
    needs_review_json = OUTPUT_DIR / f"{lecture_id}_needs_review.json"
    examples_json = OUTPUT_DIR / f"{lecture_id}_examples.json"

    if not events_path.exists():
        print(f"[skip] {lecture_id}: no frame events found at {events_path}", file=sys.stderr)
        return False
    if not slides_path.exists():
        print(f"[skip] {lecture_id}: no extracted slides found at {slides_path}", file=sys.stderr)
        return False

    if output_json.exists() and not force:
        print(f"[skip] {lecture_id}: {output_json} already exists (use --force to redo)")
        return True

    events = load_json(events_path)
    slides = load_json(slides_path)

    hashes: dict[int, int] = {}

    if not events:
        print(f"[{lecture_id}] no frame events to match, writing empty timeline")
        timeline, notes = [], ["no frame events were available to match"]
        matches = []
    else:
        print(f"[{lecture_id}] OCR'ing {len(events)} event frames (lang={ocr_lang})...")
        prev_hash, prev_ocr_text, n_deduped = None, "", 0
        for i, event in enumerate(events):
            frame_path = PROJECT_ROOT / event["frame_image_path"]
            # per-frame progress marker — OCR is this stage's long pole
            h = frame_hash(frame_path)
            hashes[i] = h
            if prev_hash is not None and hamming_distance(h, prev_hash) <= DHASH_DEDUP_THRESHOLD:
                # Near-identical to the immediately preceding event's frame
                # (e.g. a cursor-triggered false slide-change, or an
                # animation frame stage 3 also flagged) -- reuse its OCR
                # text instead of spending a second OCR call on the same
                # content.
                event["ocr_text"] = prev_ocr_text
                n_deduped += 1
                print(
                    f"  [ocr {i + 1}/{len(events)}] {frame_path.name} (near-duplicate, OCR skipped)",
                    flush=True,
                )
            else:
                print(f"  [ocr {i + 1}/{len(events)}] {frame_path.name}", flush=True)
                event["ocr_text"] = ocr.image_to_text(frame_path, ocr_lang)
            prev_hash, prev_ocr_text = h, event["ocr_text"]
        if n_deduped:
            print(f"[{lecture_id}] skipped OCR for {n_deduped}/{len(events)} near-duplicate frame(s)")

        slide_numbers = sorted(s["slide_number"] for s in slides)
        slide_refs = build_slide_reference_texts(slides)
        event_texts = [e["ocr_text"] for e in events]
        slide_texts = [slide_refs[n] for n in slide_numbers]

        print(
            f"[{lecture_id}] computing TF-IDF cosine similarity ({len(events)} events x {len(slide_numbers)} slides)..."
        )
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

        video_duration = media_probe.get_duration(INPUT_VIDEOS_DIR / f"{lecture_id}.mp4")
        timeline, notes = collapse_to_timeline(matches, video_duration)

    needs_review = build_needs_review(matches, timeline, slides, confidence_threshold)

    if detect_examples:
        example_candidates = detect_example_candidates(
            matches,
            slides,
            hashes,
            example_score_max,
            example_ink_delta,
            example_ink_text_overlap_min,
            example_ink_novel_word_min,
        )
    else:
        example_candidates = []

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    save_json(output_json, {"timeline": timeline, "notes": notes})
    save_json(needs_review_json, needs_review)
    save_json(examples_json, {"candidates": example_candidates})

    n_flags = (
        len(needs_review["low_confidence_matches"])
        + len(needs_review["unmatched_slides"])
        + len(needs_review["backward_jumps"])
    )
    print(
        f"[done] {lecture_id}: {len(timeline)} slide(s) in timeline -> {output_json} "
        f"({n_flags} item(s) flagged -> {needs_review_json}, "
        f"{len(example_candidates)} example candidate(s) -> {examples_json})"
    )
    return True


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
    parser.add_argument(
        "--all", action="store_true", help="process every lecture found in output/frame_events/"
    )
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
        default=os.environ.get("OCR_LANG", "srp_latn+eng"),
        help='tesseract language(s) (default: env OCR_LANG or "srp_latn+eng", matching webui/config.py)',
    )
    parser.add_argument(
        "--no-examples",
        action="store_true",
        help="skip worked-example candidate detection (writes an empty _examples.json)",
    )
    parser.add_argument(
        "--example-score-max",
        type=float,
        default=DEFAULT_EXAMPLE_SCORE_MAX,
        help=(
            "match score below which a frame is flagged as a 'whiteboard' example "
            f"candidate (default: {DEFAULT_EXAMPLE_SCORE_MAX})"
        ),
    )
    parser.add_argument(
        "--example-ink-delta",
        type=int,
        default=DEFAULT_EXAMPLE_INK_DELTA,
        help=(
            "dHash Hamming distance from a run's first frame above which a frame is "
            f"flagged as an 'annotated_slide' example candidate (default: {DEFAULT_EXAMPLE_INK_DELTA})"
        ),
    )
    parser.add_argument(
        "--example-ink-text-overlap-min",
        type=float,
        default=DEFAULT_EXAMPLE_INK_TEXT_OVERLAP_MIN,
        help=(
            "minimum fraction of a run's first frame's OCR words that must still appear "
            "in a candidate frame's OCR text before dHash drift counts as 'annotated_slide' "
            f"rather than a different, misgrouped slide (default: {DEFAULT_EXAMPLE_INK_TEXT_OVERLAP_MIN})"
        ),
    )
    parser.add_argument(
        "--example-ink-novel-word-min",
        type=float,
        default=DEFAULT_EXAMPLE_INK_NOVEL_WORD_MIN,
        help=(
            "minimum fraction of a candidate frame's OCR words that must be ABSENT from "
            "the deck's own extracted text for that slide before dHash drift counts as "
            "'annotated_slide' rather than a PowerPoint animation build revealing more of "
            f"the deck's own printed content (default: {DEFAULT_EXAMPLE_INK_NOVEL_WORD_MIN})"
        ),
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

    failures = []
    for lecture_id in lecture_ids:
        ok = process_lecture(
            lecture_id,
            margin=args.margin,
            confidence_threshold=args.confidence_threshold,
            force=args.force,
            ocr_lang=args.ocr_lang,
            stay_margin=args.stay_margin,
            min_forward_score=args.min_forward_score,
            detect_examples=not args.no_examples,
            example_score_max=args.example_score_max,
            example_ink_delta=args.example_ink_delta,
            example_ink_text_overlap_min=args.example_ink_text_overlap_min,
            example_ink_novel_word_min=args.example_ink_novel_word_min,
        )
        if not ok:
            failures.append(lecture_id)

    if failures:
        print(f"FAILED: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
