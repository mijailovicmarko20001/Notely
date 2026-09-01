"""Visual segmentation: build a slide timeline from the *video alone*, for
lectures that don't meaningfully use the slide deck.

The rest of the pipeline treats the deck as its spine: stage 4 matches frames
to slide numbers, stage 5 slices the transcript by those windows, stage 6
writes one note per window. That works when the professor presents slides. It
fails completely when they don't -- and not loudly. Because
matching.match_events_to_slides always picks a best candidate (there is no "no
match" outcome), and because stay_margin/min_forward_score both suppress
advancing when every score is noise, a lecture with no on-screen deck
collapses into one or two enormous timeline runs. Measured on this project's
own ASM exercise lecture: 445 frame events across 104 minutes -> 3 runs,
confidence 0.04-0.15 (every one below stage 4's own 0.25 review threshold).
Stage 5 then emits two notes each holding ~50 minutes of transcript, and stage
6 tries to summarize that. The output isn't wrong so much as useless.

This module replaces the deck with the video's own visual structure. Frame
events (stage 3) are OCR'd exactly as in deck mode, then grouped into runs of
"same thing on screen" by comparing their OCR text. Each run becomes a
timeline entry with the same JSON shape stage 4 already writes, so **stages 5,
6, 7 and 8 need no changes** -- they consume a timeline, and are indifferent
to whether its entries came from a deck or from clustering.

Why text and not pixels
-----------------------
example_detect.frame_hash (dHash) is the obvious tool and the wrong one here.
It was calibrated for slides plus annotation, where the frame is static and ink
accumulates. A screencast violates that: scrolling shifts every pixel, so dHash
distance explodes while the content is unchanged, and a single scrolled
notebook shatters into dozens of spurious segments. OCR text is inherently
scroll-invariant -- the same words are present wherever they sit on screen.

Why TF-IDF and not raw word overlap
-----------------------------------
Screencast frames share a large constant vocabulary: browser tab titles, the
URL bar, the window chrome, a video-call participant strip. On the measured
lecture that boilerplate is the single most common text on screen, so
example_detect.ocr_text_overlap (a plain containment ratio) rates every pair of
frames as highly similar and finds no boundaries at all. TF-IDF's inverse
document frequency solves this for free and without a hand-maintained
stopword list: a token appearing in nearly every frame gets a near-zero weight,
so the comparison is driven by whatever is actually changing.
"""

import sys

# --- Tuning knobs (see segment_events for how they're used) ---

# Cosine similarity between consecutive frames' TF-IDF vectors, below which a
# boundary is placed. Deliberately compared *consecutively* rather than against
# a running centroid or the run's first frame: in a screencast, content drifts
# continuously (scrolling through a notebook) while topic changes are abrupt
# (switching to a new section, opening another page, cutting to the call's
# gallery view). Consecutive comparison fires on the abrupt change and ignores
# the drift, which is the distinction we actually want. An anchor-based
# comparison splits as soon as you scroll away from the anchor; a centroid
# drifts along with the content and then over-splits long runs, since averaging
# more vectors lowers similarity to any single one.
DEFAULT_SIMILARITY_THRESHOLD = 0.35

# Minimum wall-clock duration for a segment. Consecutive comparison is
# sensitive to a single bad OCR frame (a transient dialog, a mid-scroll
# smear), which produces a one-frame island; merging anything shorter than
# this into its predecessor absorbs that noise without needing OCR to be
# reliable frame-to-frame. Also sets the floor on how fine-grained a note can
# be -- a study note covering less than ~45s of lecture is rarely worth its own
# heading.
DEFAULT_MIN_SEGMENT_SECONDS = 45.0

# Ignore tokens appearing in only one frame when building the vocabulary: on
# OCR'd screencast text those are overwhelmingly recognition garbage (misread
# glyphs, partial words at a scroll boundary) rather than real content, and
# they add noise to every comparison.
DEFAULT_MIN_DF = 2

# How many high-weight tokens to record per segment. Not used by any downstream
# stage -- purely so a human reading slide_timelines/<id>.json can tell what a
# segment was about without opening its frames, the same debugging role
# needs_review.json plays for deck mode.
SEGMENT_KEYWORD_COUNT = 8

# Stage 3's own default sampling interval is 1.5s, chosen so a quick slide flip
# isn't missed entirely. Visual mode doesn't need anything like that
# resolution: DEFAULT_MIN_SEGMENT_SECONDS already refuses to emit a segment
# shorter than 45s, so sampling three times a minute still puts ~11 frames
# inside the shortest possible segment -- far more than enough to locate a
# boundary. At 1.5s a 104-minute lecture produced 445 frame events and ~12
# minutes of OCR; most of those frames were redundant for this purpose.
#
# Exposed as a constant rather than silently overriding stage 3's default,
# because stage 3 runs before (and knows nothing about) the mode stage 4 will
# use -- the web UI fills it in when you pick visual mode, and CLI users pass
# --interval themselves. See the note in scripts/03_detect_slide_changes.py.
DEFAULT_VISUAL_INTERVAL = 4.0


def build_frame_matrix(texts):
    """TF-IDF matrix over frame OCR texts (frames are documents), plus the
    fitted vectorizer's feature names.

    Returns (dense_matrix, feature_names). Returns (None, []) when there is
    nothing to vectorize -- no frames, all-empty OCR, or a vocabulary that
    comes out empty after min_df filtering (e.g. a lecture whose frames are a
    talking head with no readable text at all). Callers treat that as "no
    usable visual signal" rather than an error; see build_visual_timeline.
    """
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer

    if not texts or not any(t.strip() for t in texts):
        return None, []

    # sublinear_tf dampens a token repeated many times in one frame (a column
    # of identical row labels in a dataframe printout, a repeated axis tick),
    # which would otherwise dominate that frame's vector.
    vectorizer = TfidfVectorizer(min_df=DEFAULT_MIN_DF, sublinear_tf=True)
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError:
        # Empty vocabulary after min_df filtering.
        return None, []
    if matrix.shape[1] == 0:
        return None, []
    return np.asarray(matrix.todense()), list(vectorizer.get_feature_names_out())


def _merge_short_segments(segments, timestamps, min_seconds):
    """Merge any segment shorter than min_seconds into its predecessor.

    Merges backward (into the previous segment) rather than forward, so a
    noise island is absorbed by the topic it interrupted rather than being
    prepended to the next one. The first segment has no predecessor and is
    therefore always kept, however short -- it holds the opening of the
    lecture, which stage 5 would otherwise have nowhere to attach.
    """
    merged = []
    for segment in segments:
        duration = timestamps[segment[-1]] - timestamps[segment[0]] if len(segment) > 1 else 0.0
        if merged and duration < min_seconds:
            merged[-1].extend(segment)
        else:
            merged.append(list(segment))
    return merged


def segment_events(matrix, timestamps, threshold, min_seconds):
    """Group frame indices into runs of "same thing on screen".

    Places a boundary wherever consecutive frames' cosine similarity drops
    below `threshold`, then merges anything shorter than `min_seconds` (see
    _merge_short_segments). Returns a list of lists of frame indices, covering
    every frame exactly once and in order.
    """
    from sklearn.metrics.pairwise import cosine_similarity

    n = len(timestamps)
    if n == 0:
        return []
    if matrix is None:
        # No usable text signal: one segment spanning everything. Degrades to
        # exactly what deck mode produces today rather than inventing
        # boundaries from nothing.
        return [list(range(n))]

    segments, current = [], [0]
    for i in range(1, n):
        similarity = float(cosine_similarity(matrix[i : i + 1], matrix[i - 1 : i])[0][0])
        if similarity < threshold:
            segments.append(current)
            current = [i]
        else:
            current.append(i)
    segments.append(current)

    return _merge_short_segments(segments, timestamps, min_seconds)


def segment_keywords(matrix, feature_names, indices, count=SEGMENT_KEYWORD_COUNT):
    """The highest mean-TF-IDF tokens across a segment's frames -- a rough
    label for what was on screen, for human inspection only."""
    import numpy as np

    if matrix is None or not feature_names or not indices:
        return []
    scores = matrix[indices].mean(axis=0)
    top = np.argsort(scores)[::-1][:count]
    return [feature_names[j] for j in top if scores[j] > 0]


def build_visual_timeline(
    events,
    ocr_texts,
    video_duration=None,
    threshold=DEFAULT_SIMILARITY_THRESHOLD,
    min_seconds=DEFAULT_MIN_SEGMENT_SECONDS,
):
    """Build a stage-4-shaped timeline from frame events and their OCR text.

    `events` are stage 3's frame events; `ocr_texts` is the parallel list of
    OCR text per event (matching.process_lecture already produces this, and
    reuses it here rather than OCR-ing twice).

    Returns (timeline, notes) exactly like matching.collapse_to_timeline:

        timeline: [{slide_number, start, end, confidence,
                    last_frame_image_path, segment_keywords}]
        notes:    [str]

    `slide_number` is a 1-based *segment* index, not a deck page. Nothing
    downstream interprets it as a page number -- stage 5 groups by it and stage
    6 renumbers for presentation anyway -- but the timeline JSON records
    "mode": "visual" at the top level (see matching.process_lecture) so stage 5
    knows to skip the duplicate-slide canonicalization that only makes sense
    for a real deck.

    `confidence` is the mean consecutive-frame similarity *within* the segment
    -- how internally coherent it is, not how well it matched anything. It is
    deliberately on the same 0-1 scale as deck mode's confidence so the web
    UI's review view renders it without a special case, but it answers a
    different question and a low value here means "the screen kept changing
    during this stretch", not "this might be the wrong slide".

    The last segment's end follows the same three-way fallback as deck mode:
    the video's true duration when known, else the last event's own timestamp
    (with an explanatory note), else None.
    """
    from sklearn.metrics.pairwise import cosine_similarity

    notes = []
    if not events:
        return [], ["no frame events were available to segment"]

    timestamps = [e["timestamp"] for e in events]
    matrix, feature_names = build_frame_matrix(ocr_texts)
    if matrix is None:
        notes.append(
            "no usable OCR text across this lecture's frames -- the whole "
            "lecture is one segment; visual segmentation had nothing to work with"
        )

    segments = segment_events(matrix, timestamps, threshold, min_seconds)

    timeline = []
    for idx, segment in enumerate(segments):
        start = timestamps[segment[0]]
        if idx + 1 < len(segments):
            end = timestamps[segments[idx + 1][0]]
        elif video_duration is not None:
            end = video_duration
        else:
            end = timestamps[segment[-1]]
            notes.append(
                f"last segment (slide_number={idx + 1}) end uses the last frame "
                "event's timestamp, not true video end, because ffprobe/video "
                "was unavailable to determine actual video duration"
            )

        if matrix is not None and len(segment) > 1:
            sims = [float(cosine_similarity(matrix[j : j + 1], matrix[j - 1 : j])[0][0]) for j in segment[1:]]
            confidence = sum(sims) / len(sims)
        else:
            confidence = 0.0

        timeline.append(
            {
                "slide_number": idx + 1,
                "start": start,
                "end": end,
                "confidence": round(float(confidence), 4),
                # Last frame of the run, same reasoning as deck mode's
                # last_frame_image_path: on a screencast the content of a
                # segment accumulates (code written, a plot rendered, a
                # diagram completed), so the final frame is its most complete
                # capture and the most useful one to show a reader.
                "last_frame_image_path": events[segment[-1]]["frame_image_path"],
                "segment_keywords": segment_keywords(matrix, feature_names, segment),
            }
        )

    print(
        f"  visual segmentation: {len(events)} frame event(s) -> {len(timeline)} segment(s) "
        f"(threshold={threshold}, min_seconds={min_seconds})",
        file=sys.stderr,
    )
    return timeline, notes
