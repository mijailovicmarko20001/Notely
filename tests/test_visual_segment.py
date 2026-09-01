"""notely.pipeline.visual_segment -- building a timeline from the video alone,
for lectures that don't present slides.

Pure logic against synthetic OCR text: no OCR engine, no video, no deck. The
inputs here deliberately mimic the shape of the real failure this module was
written for (see its module docstring): a screencast whose frames all share a
large constant vocabulary (browser chrome, window titles) with the actually-
changing content in the minority.
"""

import pytest

from notely.pipeline.visual_segment import (
    build_frame_matrix,
    build_visual_timeline,
    segment_events,
    segment_keywords,
)

# Every frame carries the same "browser chrome", exactly as a real screencast
# does -- the thing that defeats a plain word-overlap comparison.
#
# The chrome-to-content ratio here is deliberately realistic (roughly a third
# of the tokens), because the effect being tested is ratio-sensitive: IDF
# down-weights a token present in every frame, but it cannot make it weightless,
# so a fixture that is 90% chrome and one topic word stays above any sane
# threshold and never splits. Measured frames from this project's own ASM
# lecture run 60-250 OCR'd words of which chrome is ~20-40. Note also that IDF
# separation strengthens with corpus size (chrome in 445 of 445 frames is
# suppressed far harder than chrome in 4 of 4), so these fixtures use enough
# frames for the effect to be visible at all.
CHROME = "localhost jupyter notebook file edit view kernel chrome"

TOPICS = {
    "a": "centrality degree betweenness closeness eigenvector adjacency neighbour",
    "b": "pagerank damping teleport random walk stochastic convergence iteration",
    "c": "louvain modularity community partition resolution cluster greedy",
    "d": "dataframe pandas merge groupby column index csv dtype",
}


def frame(topic_key: str) -> str:
    """One frame's OCR text: constant chrome plus one topic's vocabulary."""
    return f"{CHROME} {TOPICS[topic_key]}"


def _events(timestamps):
    return [{"timestamp": t, "frame_image_path": f"output/f/{i:03d}.png"} for i, t in enumerate(timestamps)]


# --- build_frame_matrix ----------------------------------------------------


def test_build_frame_matrix_returns_none_for_no_texts():
    matrix, names = build_frame_matrix([])
    assert matrix is None and names == []


def test_build_frame_matrix_returns_none_when_all_text_is_blank():
    """A talking-head lecture OCRs to nothing; that's 'no signal', not an error."""
    matrix, names = build_frame_matrix(["", "   ", "\n"])
    assert matrix is None and names == []


def test_build_frame_matrix_returns_none_when_vocabulary_is_empty_after_min_df():
    """min_df=2 drops tokens seen in only one frame; if that's everything,
    there is no usable vocabulary left."""
    matrix, names = build_frame_matrix(["alpha", "beta", "gamma"])
    assert matrix is None and names == []


def test_build_frame_matrix_shape_is_frames_by_vocabulary():
    texts = [frame("a"), frame("a"), frame("b")]
    matrix, names = build_frame_matrix(texts)
    assert matrix.shape[0] == 3
    assert matrix.shape[1] == len(names)


def test_idf_suppresses_boilerplate_shared_by_every_frame():
    """The whole reason this module uses TF-IDF rather than word overlap:
    tokens present in every frame must end up weighted at ~0, so the
    comparison is driven by what's actually changing."""
    texts = [frame("a"), frame("a"), frame("b"), frame("b")]
    matrix, names = build_frame_matrix(texts)

    idx = {n: i for i, n in enumerate(names)}
    chrome_weight = max(matrix[:, idx[w]].max() for w in ("localhost", "jupyter", "notebook") if w in idx)
    topic_weight = max(matrix[:, idx[w]].max() for w in ("centrality", "pagerank") if w in idx)
    assert chrome_weight < topic_weight, (
        f"boilerplate ({chrome_weight:.3f}) should weigh less than topic words ({topic_weight:.3f})"
    )


# --- segment_events --------------------------------------------------------


def test_segment_events_empty_input():
    assert segment_events(None, [], 0.35, 45.0) == []


def test_segment_events_without_matrix_is_one_segment():
    """No usable text -> don't invent boundaries; degrade to a single segment."""
    assert segment_events(None, [0.0, 10.0, 20.0], 0.35, 45.0) == [[0, 1, 2]]


def test_segment_events_splits_on_topic_change_despite_shared_chrome():
    texts = [frame("a")] * 4 + [frame("b")] * 4
    timestamps = [i * 60.0 for i in range(8)]
    matrix, _ = build_frame_matrix(texts)

    segments = segment_events(matrix, timestamps, 0.35, 45.0)

    assert len(segments) == 2
    assert segments[0] == [0, 1, 2, 3]
    assert segments[1] == [4, 5, 6, 7]


def test_segment_events_does_not_split_identical_frames():
    texts = [frame("a")] * 6
    timestamps = [i * 60.0 for i in range(6)]
    matrix, _ = build_frame_matrix(texts)

    assert segment_events(matrix, timestamps, 0.35, 45.0) == [[0, 1, 2, 3, 4, 5]]


def test_segment_events_covers_every_frame_exactly_once_and_in_order():
    texts = [frame(k) for k in ("a", "a", "b", "c", "c", "b")]
    timestamps = [i * 60.0 for i in range(6)]
    matrix, _ = build_frame_matrix(texts)

    segments = segment_events(matrix, timestamps, 0.5, 1.0)

    flat = [i for seg in segments for i in seg]
    assert flat == sorted(flat) == list(range(6))


def test_short_segments_are_merged_into_their_predecessor():
    """A brief island (a transient dialog, a detour to another window) must be
    absorbed rather than becoming its own note.

    The interloper spans two frames, not one, on purpose: min_df=2 drops any
    token appearing in a single frame, so a one-frame detour has no
    distinguishing vocabulary left to split on in the first place. That's the
    intended noise suppression (see DEFAULT_MIN_DF), and it means the smallest
    visual state that can form its own segment is one seen at least twice.
    """
    texts = [frame("a")] * 3 + [frame("d")] * 2 + [frame("a")] * 2
    # the interloper spans 1s; everything else is minutes apart
    timestamps = [0.0, 60.0, 120.0, 121.0, 122.0, 180.0, 240.0]
    matrix, _ = build_frame_matrix(texts)

    unmerged = segment_events(matrix, timestamps, 0.5, 0.0)
    merged = segment_events(matrix, timestamps, 0.5, 45.0)

    assert len(unmerged) > len(merged), "the short detour should have been its own segment before merging"
    assert [i for seg in merged for i in seg] == list(range(7))


def test_first_segment_is_kept_even_when_shorter_than_the_minimum():
    """It has no predecessor to merge into, and holds the lecture's opening."""
    texts = [frame("a"), frame("b"), frame("b"), frame("b")]
    timestamps = [0.0, 1.0, 120.0, 240.0]
    matrix, _ = build_frame_matrix(texts)

    segments = segment_events(matrix, timestamps, 0.5, 45.0)

    assert segments[0][0] == 0
    assert [i for seg in segments for i in seg] == [0, 1, 2, 3]


# --- segment_keywords ------------------------------------------------------


def test_segment_keywords_surface_topic_words_not_boilerplate():
    texts = [frame("a")] * 3 + [frame("b")] * 3
    matrix, names = build_frame_matrix(texts)

    words = segment_keywords(matrix, names, [0, 1, 2], count=3)

    assert "centrality" in words
    assert "localhost" not in words


def test_segment_keywords_empty_when_no_matrix():
    assert segment_keywords(None, [], [0, 1]) == []


# --- build_visual_timeline -------------------------------------------------


def test_build_visual_timeline_no_events():
    timeline, notes = build_visual_timeline([], [])
    assert timeline == []
    assert notes and "no frame events" in notes[0]


def test_build_visual_timeline_shape_matches_deck_mode():
    """Stages 5-8 consume this artifact and must not need to care which mode
    produced it -- the keys deck mode writes have to all be present."""
    texts = [frame("a")] * 3 + [frame("b")] * 3
    events = _events([i * 60.0 for i in range(6)])

    timeline, _ = build_visual_timeline(events, texts, video_duration=400.0)

    assert timeline
    for entry in timeline:
        assert set(entry) >= {
            "slide_number",
            "start",
            "end",
            "confidence",
            "last_frame_image_path",
        }


def test_build_visual_timeline_numbers_segments_from_one_contiguously():
    texts = [frame("a")] * 3 + [frame("b")] * 3
    events = _events([i * 60.0 for i in range(6)])

    timeline, _ = build_visual_timeline(events, texts, video_duration=400.0)

    assert [e["slide_number"] for e in timeline] == list(range(1, len(timeline) + 1))


def test_build_visual_timeline_windows_are_contiguous_and_end_at_video_duration():
    texts = [frame("a")] * 3 + [frame("b")] * 3
    events = _events([i * 60.0 for i in range(6)])

    timeline, _ = build_visual_timeline(events, texts, video_duration=400.0)

    assert timeline[0]["start"] == 0.0
    for a, b in zip(timeline, timeline[1:], strict=False):
        assert a["end"] == b["start"], "no gap/overlap between consecutive segments"
    assert timeline[-1]["end"] == 400.0


def test_build_visual_timeline_falls_back_to_last_timestamp_without_duration():
    texts = [frame("a")] * 3 + [frame("b")] * 3
    events = _events([i * 60.0 for i in range(6)])

    timeline, notes = build_visual_timeline(events, texts, video_duration=None)

    assert timeline[-1]["end"] == 300.0
    assert any("true video end" in n for n in notes)


def test_build_visual_timeline_uses_last_frame_of_each_segment():
    """Same reasoning as deck mode's last_frame_image_path: on a screencast the
    content accumulates, so the final frame is the most complete capture."""
    texts = [frame("a")] * 3 + [frame("b")] * 3
    events = _events([i * 60.0 for i in range(6)])

    timeline, _ = build_visual_timeline(events, texts, video_duration=400.0)

    assert timeline[0]["last_frame_image_path"] == "output/f/002.png"
    assert timeline[-1]["last_frame_image_path"] == "output/f/005.png"


def test_build_visual_timeline_notes_when_there_is_no_ocr_signal():
    events = _events([0.0, 60.0, 120.0])

    timeline, notes = build_visual_timeline(events, ["", "", ""], video_duration=200.0)

    assert len(timeline) == 1, "no signal collapses to a single segment"
    assert any("no usable OCR text" in n for n in notes)


def test_build_visual_timeline_confidence_is_within_unit_interval():
    texts = [frame("a")] * 3 + [frame("b")] * 3
    events = _events([i * 60.0 for i in range(6)])

    timeline, _ = build_visual_timeline(events, texts, video_duration=400.0)

    for entry in timeline:
        assert 0.0 <= entry["confidence"] <= 1.0


@pytest.mark.parametrize("threshold", [0.1, 0.35, 0.6, 0.9])
def test_build_visual_timeline_always_covers_the_whole_lecture(threshold):
    """Whatever the tuning, every second between the first and last event must
    belong to exactly one segment -- stage 5 assigns transcript by these
    windows, so a gap silently drops speech from the notes."""
    texts = [frame(k) for k in ("a", "b", "c", "a", "d", "b", "c", "d")]
    events = _events([i * 60.0 for i in range(8)])

    timeline, _ = build_visual_timeline(events, texts, video_duration=500.0, threshold=threshold)

    assert timeline[0]["start"] == 0.0
    assert timeline[-1]["end"] == 500.0
    for a, b in zip(timeline, timeline[1:], strict=False):
        assert a["end"] == b["start"]
