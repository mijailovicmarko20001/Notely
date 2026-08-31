"""Worked-example candidate detection for stage [4] frame-to-slide
matching -- moved here from scripts/04_match_frames_to_slides.py (Phase 5),
split out of notely.pipeline.matching.

Flags frame events that likely show the professor working a concrete
example -- off-deck (whiteboard/tablet), annotated live over a slide, or
the deck's own dedicated example slide -- so stage 6 can offer them to an
LLM for confirmation and captioning. Always computed (free, local
heuristics); confirmation/captioning happens downstream, gated behind
NOTES_DETECT_EXAMPLES. See CLAUDE.md for the full feature.

Also holds frame_hash/hamming_distance/group_runs/OCR_EXCERPT_LEN: pieces
notely.pipeline.matching needs too (OCR-dedup pre-pass, timeline
collapsing, OCR-excerpt truncation). They live here (which has no
dependency on matching.py) rather than in matching.py or a third shared
module, so the dependency graph stays one-directional -- matching.py
imports from here, this module never imports from matching.py.
"""

import re

from ..text import fold_diacritics as _fold_diacritics

OCR_EXCERPT_LEN = 150

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
# ~0 "novel" words even though it visually differs a lot, pixel-wise, from
# the run's first frame and even from the deck's own rendered image at that
# instant -- it just hasn't been fully built out yet, nothing was added
# beyond print. Real handwritten/drawn ink, by contrast, is never in the
# deck's source text at all, so it always shows up as novel words here.
# Calibrated against real false positives found on this course's footage: a
# dense multi-box slide mid-build measured at exactly 0.0 novel, comfortably
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


def frame_hash(image_path, hash_size: int = DHASH_SIZE) -> int:
    """Difference hash (dHash) of a frame image: resize to (n+1)xn grayscale,
    then one bit per pixel for whether it's darker than its right neighbor.
    Cheap (~ms) and dependency-free (PIL only, already required for OCR) way
    to catch near-identical consecutive frames before spending an OCR call
    on both -- OCR is this stage's own long pole (see the per-frame progress
    marker in notely.pipeline.matching)."""
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


def group_runs(matches: list[dict]) -> list[list[dict]]:
    """Group consecutive matches with the same slide_number into runs.

    Factored out of notely.pipeline.matching.collapse_to_timeline so
    example detection (detect_example_candidates) groups events identically
    to the timeline it augments -- the two must never drift apart, or a
    candidate's "run" could disagree with the timeline entry it's meant to
    attach to.
    """
    runs = []
    for m in matches:
        if runs and runs[-1][-1]["slide_number"] == m["slide_number"]:
            runs[-1].append(m)
        else:
            runs.append([m])
    return runs


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
    raw text is at (or near) OCR_EXCERPT_LEN -- notely.pipeline.matching's
    match_events_to_slides truncates OCR text to that length, so a
    candidate at the boundary very likely had its last word cut off
    mid-word rather than ending where the professor's slide actually did,
    which would otherwise register as a spurious "novel" word on every
    single long excerpt (observed on real footage: "proizvoljnim"
    truncated to "proizv", not in the deck's own text as a fragment even
    though the full word is).

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
