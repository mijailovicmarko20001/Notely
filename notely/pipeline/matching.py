"""Stage [4]: frame-to-slide matching -- moved here from
scripts/04_match_frames_to_slides.py (Phase 5), which is now a thin CLI
wrapper around process_lecture().

For each frame-change event detected in stage [3], figure out which slide
number it actually is, using OCR + TF-IDF text similarity constrained by
the assumption that slides are shown roughly in sequential order. See
CLAUDE.md's "[4] Frame-to-slide matching" for the full method.
Worked-example candidate detection lives in notely.pipeline.example_detect.
"""

import os
import sys

from ..adapters.ffprobe_media_probe import FfprobeMediaProbe
from ..adapters.tesseract_ocr import TesseractOcr
from ..io import load_json, save_json
from ..paths import PROJECT_ROOT
from ..paths import VIDEOS_DIR as INPUT_VIDEOS_DIR
from .example_detect import (
    DEFAULT_EXAMPLE_INK_DELTA,
    DEFAULT_EXAMPLE_INK_NOVEL_WORD_MIN,
    DEFAULT_EXAMPLE_INK_TEXT_OVERLAP_MIN,
    DEFAULT_EXAMPLE_SCORE_MAX,
    DHASH_DEDUP_THRESHOLD,
    OCR_EXCERPT_LEN,
    detect_example_candidates,
    frame_hash,
    group_runs,
    hamming_distance,
)
from .visual_segment import (
    DEFAULT_MIN_SEGMENT_SECONDS,
    DEFAULT_SIMILARITY_THRESHOLD,
    build_visual_timeline,
)

# Stage 4 has two ways to build a timeline. "deck" is the original: OCR each
# frame, match it to a slide number by text similarity, constrained by the
# sequential-order assumption. "visual" ignores the deck entirely and groups
# frames by what's on screen -- for lectures that don't present slides, where
# deck mode silently collapses the whole lecture into one or two runs (see
# notely.pipeline.visual_segment's module docstring for the measured failure).
MODE_DECK = "deck"
MODE_VISUAL = "visual"
MODES = (MODE_DECK, MODE_VISUAL)

FRAME_EVENTS_DIR = PROJECT_ROOT / "output" / "frame_events"
SLIDES_EXTRACTED_DIR = PROJECT_ROOT / "output" / "slides_extracted"
OUTPUT_DIR = PROJECT_ROOT / "output" / "slide_timelines"

# --- Tuning knobs for the sequential-order constraint (see
# match_events_to_slides for how they're used) ---
DEFAULT_BACKWARD_JUMP_MARGIN = 0.15
DEFAULT_STAY_MARGIN = 0.05
DEFAULT_CONFIDENCE_THRESHOLD = 0.25
DEFAULT_MIN_FORWARD_SCORE = 0.05

# Fraction of a deck-mode run's matches that may fall below
# confidence_threshold before the deck is judged to be absent from the screen
# and the timeline is rebuilt with visual segmentation instead (see
# should_fall_back_to_visual).
#
# A lecture that never shows its deck doesn't fail loudly -- every match still
# gets *a* slide number, just a meaningless one. Measured on a real deckless
# lecture: 445 events, every match below this threshold, timeline collapsed to
# 3 runs across 104 minutes. So "almost everything is low-confidence" is the
# available signal.
#
# 0.60 is a deliberate, and deliberately *reversible*, choice rather than a
# calibrated one: there was no known-good deck-using lecture on hand to verify
# the detector doesn't also fire on one (a legitimately-matched but
# OCR-hostile lecture -- formula-heavy slides Tesseract mangles -- can carry a
# lot of low-confidence matches too). The failure it risks is bounded: a false
# positive yields visual-mode notes, which are useful, not wrong, and the
# switch is announced on stdout and recorded in the timeline's own notes. Rerun
# with an explicit --mode deck --force to override. Set to 0 to disable.
DEFAULT_AUTO_VISUAL_THRESHOLD = 0.60


def _ocr_concurrency() -> int:
    """Worker count for the OCR pass. Threads (not processes) because the
    work happens outside the GIL either way: pytesseract shells out to the
    tesseract binary, and PIL's decode/resize releases it too.

    Defaults to the machine's core count, capped at 8 -- past that, tesseract
    processes contend for memory bandwidth more than they gain from
    parallelism. OCR_CONCURRENCY overrides, same convention as
    NOTES_CONCURRENCY."""
    configured = os.environ.get("OCR_CONCURRENCY")
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            pass
    return max(1, min(8, os.cpu_count() or 4))


def plan_ocr(hashes: dict[int, int], n_events: int) -> list[int]:
    """Decide which event index each event takes its OCR text from.

    Returns `owner`, where owner[i] == i means "OCR this frame" and
    owner[i] < i means "reuse the text already produced for that earlier
    frame". An event whose frame is near-identical to the *immediately
    preceding* event's (dHash within DHASH_DEDUP_THRESHOLD) inherits that
    event's owner, so a run of near-duplicates all resolve to the single
    frame at the head of the run -- which is exactly what the original
    sequential loop did by carrying `prev_ocr_text` forward.

    Kept as its own pure function so the dedup rule stays readable and
    testable independently of the concurrency around it: this is the part
    that must remain strictly sequential (each decision depends on its
    predecessor), while the OCR calls it schedules are independent.
    """
    owner = [0] * n_events
    for i in range(n_events):
        prev, cur = hashes.get(i - 1), hashes.get(i)
        near_duplicate = (
            i > 0
            and prev is not None
            and cur is not None
            and hamming_distance(cur, prev) <= DHASH_DEDUP_THRESHOLD
        )
        owner[i] = owner[i - 1] if near_duplicate else i
    return owner


def ocr_events(events: list[dict], hashes: dict[int, int], ocr, ocr_lang: str) -> int:
    """Fill each event's "ocr_text", skipping frames that duplicate their
    predecessor. Populates `hashes` (event index -> dHash) in place and
    returns how many OCR calls were skipped.

    Three phases: hash every frame, decide which frames actually need OCR
    (plan_ocr -- sequential by nature), then run those OCR calls
    concurrently. OCR is this stage's long pole by a wide margin (measured:
    445 frames at ~1.7s each, ~12 minutes, on one core of an otherwise idle
    machine), and the calls are independent once the dedup plan is fixed --
    the same reasoning that already makes stage 6 issue its per-slide API
    calls from a thread pool. Results are collected by index, so the
    resulting texts are identical to the sequential version regardless of
    completion order.
    """
    from concurrent.futures import ThreadPoolExecutor

    n = len(events)
    workers = _ocr_concurrency()
    paths = [PROJECT_ROOT / e["frame_image_path"] for e in events]

    # Phase 1: hash every frame, regardless of whether it will be OCR'd --
    # example detection measures ink drift frame-to-frame, not
    # OCR-call-to-OCR-call, so it needs a hash for every event.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, h in enumerate(pool.map(frame_hash, paths)):
            hashes[i] = h

    # Phase 2: the sequential part -- who copies whose text.
    owner = plan_ocr(hashes, n)
    to_ocr = [i for i in range(n) if owner[i] == i]

    # Phase 3: the expensive part, in parallel.
    print(f"  OCR: {len(to_ocr)}/{n} frame(s) need a call, {workers} worker(s)", flush=True)
    done = 0

    def run(i):
        nonlocal done
        text = ocr.image_to_text(paths[i], ocr_lang)
        done += 1  # only ever incremented under the GIL by CPython; a rough
        # progress counter, not a synchronisation point
        print(f"  [ocr {done}/{len(to_ocr)}] {paths[i].name}", flush=True)
        return text

    with ThreadPoolExecutor(max_workers=workers) as pool:
        texts = dict(zip(to_ocr, pool.map(run, to_ocr), strict=True))

    for i, event in enumerate(events):
        event["ocr_text"] = texts[owner[i]]

    return n - len(to_ocr)


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


def low_confidence_ratio(matches: list[dict], confidence_threshold: float) -> float:
    """Fraction of matches scoring below confidence_threshold, in [0, 1].
    Returns 0.0 for an empty match list -- no evidence of failure is not
    evidence of failure."""
    if not matches:
        return 0.0
    return sum(1 for m in matches if m["score"] < confidence_threshold) / len(matches)


def should_fall_back_to_visual(
    matches: list[dict], confidence_threshold: float, auto_visual_threshold: float
) -> bool:
    """Whether a deck-mode run matched so poorly that the deck almost
    certainly wasn't on screen, and the timeline should be rebuilt by visual
    segmentation instead.

    Deliberately a single, legible condition -- the share of matches below
    confidence_threshold -- rather than a compound score. This decision
    silently changes what the whole lecture's notes are built from, so it
    needs to be something a human can check by eye against the numbers stage 4
    already prints, and reproduce from needs_review.json afterwards.

    auto_visual_threshold <= 0 disables the check entirely.
    """
    if auto_visual_threshold <= 0 or not matches:
        return False
    return low_confidence_ratio(matches, confidence_threshold) >= auto_visual_threshold


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
    ocr=None,
    media_probe=None,
    stay_margin: float = DEFAULT_STAY_MARGIN,
    min_forward_score: float = DEFAULT_MIN_FORWARD_SCORE,
    detect_examples: bool = True,
    example_score_max: float = DEFAULT_EXAMPLE_SCORE_MAX,
    example_ink_delta: int = DEFAULT_EXAMPLE_INK_DELTA,
    example_ink_text_overlap_min: float = DEFAULT_EXAMPLE_INK_TEXT_OVERLAP_MIN,
    example_ink_novel_word_min: float = DEFAULT_EXAMPLE_INK_NOVEL_WORD_MIN,
    mode: str = MODE_DECK,
    auto_visual_threshold: float = DEFAULT_AUTO_VISUAL_THRESHOLD,
    visual_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    visual_min_seconds: float = DEFAULT_MIN_SEGMENT_SECONDS,
) -> bool:
    """Returns False only when required input (frame events, and in deck mode
    the extracted slides) was missing (the caller should treat that as a
    failure); an already-done skip and a real successful run both return True.

    mode: MODE_DECK (default) matches frames to slide numbers. MODE_VISUAL
    ignores the deck and segments the lecture by what's on screen -- for
    recordings that don't present slides; see notely.pipeline.visual_segment.
    In visual mode the slide deck is optional (a lecture with no deck at all
    is the whole point), needs_review is empty (there is no match to doubt),
    and worked-example detection is skipped: its heuristics classify anything
    that doesn't resemble the deck as a "whiteboard" candidate, which in a
    deckless lecture is every single frame -- the segments themselves are
    already the extraction that feature was approximating.

    ocr: an Ocr (see notely.ports), defaults to the real pytesseract-backed
    adapter. media_probe: a MediaProbe, defaults to the real ffprobe-backed
    adapter. Both default to their real adapter; tests inject a fake
    instead of needing tesseract/ffprobe installed."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}, expected one of {MODES}")
    # May be flipped to visual below if deck matching turns out to be
    # meaningless; everything after the matching step keys off this, not `mode`.
    effective_mode = mode
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
    # Visual mode never consults the deck, so a missing one is fine there --
    # that's the case it exists for.
    if not slides_path.exists() and mode == MODE_DECK:
        print(f"[skip] {lecture_id}: no extracted slides found at {slides_path}", file=sys.stderr)
        return False

    if output_json.exists() and not force:
        print(f"[skip] {lecture_id}: {output_json} already exists (use --force to redo)")
        return True

    events = load_json(events_path)
    slides = load_json(slides_path) if slides_path.exists() else []

    hashes: dict[int, int] = {}

    if not events:
        print(f"[{lecture_id}] no frame events to match, writing empty timeline")
        timeline, notes = [], ["no frame events were available to match"]
        matches = []
    else:
        print(f"[{lecture_id}] OCR'ing {len(events)} event frames (lang={ocr_lang})...")
        n_deduped = ocr_events(events, hashes, ocr, ocr_lang)
        if n_deduped:
            print(f"[{lecture_id}] skipped OCR for {n_deduped}/{len(events)} near-duplicate frame(s)")

        video_duration = media_probe.get_duration(INPUT_VIDEOS_DIR / f"{lecture_id}.mp4")

    if events and mode == MODE_VISUAL:
        # Visual mode: the OCR pass above is all the input we need. No deck
        # comparison, no sequential-order constraint -- group the frames by
        # what's actually on screen and let each run become a note.
        print(f"[{lecture_id}] segmenting by on-screen content (no deck)...")
        timeline, notes = build_visual_timeline(
            events,
            [e["ocr_text"] for e in events],
            video_duration,
            threshold=visual_threshold,
            min_seconds=visual_min_seconds,
        )
        matches = []
        for entry in timeline:
            # Built outside the f-string: a nested same-quote f-string needs
            # PEP 701 (Python 3.12+), and this project supports 3.11.
            end_text = "?" if entry["end"] is None else f"{entry['end']:8.2f}s"
            keywords = " ".join(entry["segment_keywords"][:6])
            print(
                f"  segment {entry['slide_number']:03d} {entry['start']:8.2f}s -> {end_text} "
                f"(coherence={entry['confidence']:.3f}) {keywords}"
            )
    elif events:
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

        timeline, notes = collapse_to_timeline(matches, video_duration)

        # The deck may simply never have been on screen. Detect that from how
        # the matching went and rebuild the timeline visually rather than
        # handing stage 5 windows that don't mean anything -- see
        # should_fall_back_to_visual, and DEFAULT_AUTO_VISUAL_THRESHOLD for
        # why this is announced rather than silent.
        if should_fall_back_to_visual(matches, confidence_threshold, auto_visual_threshold):
            ratio = low_confidence_ratio(matches, confidence_threshold)
            reason = (
                f"{ratio:.0%} of {len(matches)} matches scored below the confidence "
                f"threshold ({confidence_threshold}), and the timeline collapsed to "
                f"{len(timeline)} run(s) -- this lecture does not appear to display its "
                f"slide deck, so the timeline was rebuilt by visual segmentation "
                f"(auto_visual_threshold={auto_visual_threshold}). Re-run with an "
                f"explicit --mode deck to keep the slide matching instead."
            )
            print(f"\n[{lecture_id}] !! FALLING BACK TO VISUAL MODE: {reason}\n", flush=True)
            effective_mode = MODE_VISUAL
            timeline, notes = build_visual_timeline(
                events,
                [e["ocr_text"] for e in events],
                video_duration,
                threshold=visual_threshold,
                min_seconds=visual_min_seconds,
            )
            # Recorded in the artifact too, not just stdout: whoever reads this
            # timeline later must be able to see it isn't deck-derived.
            notes.insert(0, f"AUTOMATIC FALLBACK: {reason}")
            # Every downstream consumer keys off the effective mode, and the
            # per-event slide matches no longer describe this timeline.
            matches = []

    # Visual mode has no per-frame slide match, so there is nothing to doubt:
    # every needs_review bucket is about a matching decision that wasn't made
    # here. (A segment's own coherence is already reported as its confidence.)
    if effective_mode == MODE_VISUAL:
        needs_review = {"low_confidence_matches": [], "unmatched_slides": [], "backward_jumps": []}
    else:
        needs_review = build_needs_review(matches, timeline, slides, confidence_threshold)

    # Worked-example detection is deck-relative (it asks "does this frame look
    # unlike the deck?"), which is meaningless when there is no deck -- see
    # process_lecture's docstring.
    if detect_examples and effective_mode == MODE_DECK:
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
    # "mode" is recorded so stage 5 can tell a visual timeline from a deck one:
    # its duplicate-slide canonicalization (build_canonical_slide_map) remaps
    # slide numbers through the deck's own text, which would corrupt visual
    # mode's segment indices -- they aren't deck pages and must not be remapped.
    # Absent means deck mode, so timelines written before this existed still read
    # correctly.
    save_json(output_json, {"mode": effective_mode, "timeline": timeline, "notes": notes})
    save_json(needs_review_json, needs_review)
    save_json(examples_json, {"candidates": example_candidates})

    n_flags = (
        len(needs_review["low_confidence_matches"])
        + len(needs_review["unmatched_slides"])
        + len(needs_review["backward_jumps"])
    )
    unit = "segment" if effective_mode == MODE_VISUAL else "slide"
    print(
        f"[done] {lecture_id}: {len(timeline)} {unit}(s) in timeline -> {output_json} "
        f"({n_flags} item(s) flagged -> {needs_review_json}, "
        f"{len(example_candidates)} example candidate(s) -> {examples_json})"
    )
    return True
