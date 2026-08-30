"""scripts/04_match_frames_to_slides.py: the sequential-order-constrained
matcher. Per CLAUDE.md this is "the highest-risk stage in the pipeline" --
worth pinning its margin/stay_margin/min_forward_score behavior down with
synthetic cases, on top of the manual dry-run verification already done
against real lecture01 data (see DOCUMENTATION.md and TODO.md for that
investigation)."""

import numpy as np

from conftest import load_stage

m4 = load_stage("04_match_frames_to_slides.py")


def _events(n):
    return [{"timestamp": float(i), "frame_image_path": f"event_{i}.png"} for i in range(n)]


def test_plain_in_order_progression():
    # each event most strongly matches the next slide in sequence
    sim = np.array(
        [
            [0.9, 0.1, 0.0],
            [0.1, 0.9, 0.1],
            [0.0, 0.1, 0.9],
        ]
    )
    matches = m4.match_events_to_slides(_events(3), [1, 2, 3], sim, margin=0.15, stay_margin=0.05)
    assert [m["slide_number"] for m in matches] == [1, 2, 3]
    assert not any(m["jumped_backward"] for m in matches)


def test_stay_margin_suppresses_oscillation_between_near_duplicates():
    # two consecutive near-duplicate slides (e.g. a multi-slide formula
    # derivation) score within noise of each other every event -- without
    # stay_margin the cursor would bounce 1,2,1,2 instead of committing
    sim = np.array(
        [
            [0.50, 0.48],
            [0.49, 0.52],
            [0.47, 0.51],
            [0.48, 0.53],
        ]
    )
    matches = m4.match_events_to_slides(_events(4), [1, 2], sim, margin=0.15, stay_margin=0.05)
    slides = [m["slide_number"] for m in matches]
    # should settle rather than bounce back and forth every event
    assert slides.count(1) + slides.count(2) == 4
    transitions = sum(1 for a, b in zip(slides, slides[1:], strict=False) if a != b)
    assert transitions <= 1, f"expected at most one committed transition, got {slides}"


def test_stay_margin_zero_falls_back_to_always_take_max():
    sim = np.array(
        [
            [0.50, 0.48],
            [0.49, 0.52],
        ]
    )
    matches = m4.match_events_to_slides(_events(2), [1, 2], sim, margin=0.15, stay_margin=0.0)
    # with no stickiness, event 1's raw max (slide 2, 0.52 > 0.49) wins outright
    assert matches[1]["slide_number"] == 2
    assert not matches[1]["stayed_over_raw_best"]


def test_backward_jump_requires_beating_margin():
    # event strongly matches an earlier slide, but not by more than margin
    # over staying in order -> should NOT jump back
    sim = np.array(
        [
            [0.9, 0.1, 0.1],  # event 0 -> slide 1
            [0.1, 0.9, 0.1],  # event 1 -> slide 2
            [
                0.30,
                0.10,
                0.10,
            ],  # event 2: slide1=0.30 vs current(slide2)=0.10; margin=0.15 not cleared (0.30-0.10=0.20... )
        ]
    )
    # make the margin large enough that 0.20 doesn't clear it
    matches = m4.match_events_to_slides(_events(3), [1, 2, 3], sim, margin=0.25, stay_margin=0.0)
    assert not matches[2]["jumped_backward"]
    assert matches[2]["slide_number"] == 2  # stays in order (best in-order candidate is slide 2 or 3)


def test_backward_jump_taken_when_it_clears_margin():
    sim = np.array(
        [
            [0.9, 0.1, 0.1],  # event 0 -> slide 1
            [0.1, 0.9, 0.1],  # event 1 -> slide 2
            [0.9, 0.05, 0.05],  # event 2: slide1=0.9 vs in-order best ~0.1 -> clears any reasonable margin
        ]
    )
    matches = m4.match_events_to_slides(_events(3), [1, 2, 3], sim, margin=0.25, stay_margin=0.05)
    assert matches[2]["jumped_backward"]
    assert matches[2]["slide_number"] == 1


def test_min_forward_score_blocks_near_zero_jump():
    # current slide's own score has decayed near zero (e.g. OCR failure),
    # and a forward candidate is *also* near zero but nominally higher --
    # without min_forward_score this "wins" as the raw max despite meaning
    # nothing; with it, the cursor should stay put instead.
    sim = np.array(
        [
            [0.9, 0.05, 0.02],  # event 0 -> slide 1 (settles the cursor there)
            [0.01, 0.005, 0.03],  # event 1: current(slide1)=0.01, best in-order=slide3 @0.03
        ]
    )
    matches_unguarded = m4.match_events_to_slides(
        _events(2), [1, 2, 3], sim, margin=0.15, stay_margin=0.0, min_forward_score=0.0
    )
    matches_guarded = m4.match_events_to_slides(
        _events(2), [1, 2, 3], sim, margin=0.15, stay_margin=0.0, min_forward_score=0.05
    )
    assert matches_unguarded[1]["slide_number"] == 3  # the nonsense jump, unguarded
    assert matches_guarded[1]["slide_number"] == 1  # floor keeps the cursor at slide 1


def test_min_forward_score_does_not_block_confident_forward_moves():
    sim = np.array(
        [
            [0.9, 0.1, 0.05],
            [0.1, 0.85, 0.05],  # a clearly confident advance to slide 2
        ]
    )
    matches = m4.match_events_to_slides(
        _events(2), [1, 2, 3], sim, margin=0.15, stay_margin=0.05, min_forward_score=0.05
    )
    assert matches[1]["slide_number"] == 2


def test_empty_events_returns_empty():
    assert m4.match_events_to_slides([], [1, 2, 3], np.zeros((0, 3)), margin=0.15) == []


def test_empty_slide_numbers_returns_empty():
    assert m4.match_events_to_slides(_events(2), [], np.zeros((2, 0)), margin=0.15) == []


# --- collapse_to_timeline ---------------------------------------------------


def test_collapse_merges_consecutive_same_slide_events():
    matches = [
        {"timestamp": 0.0, "slide_number": 1, "score": 0.9, "frame_image_path": "a.png"},
        {"timestamp": 1.0, "slide_number": 1, "score": 0.8, "frame_image_path": "b.png"},
        {"timestamp": 2.0, "slide_number": 2, "score": 0.7, "frame_image_path": "c.png"},
    ]
    timeline, notes = m4.collapse_to_timeline(matches, video_duration=10.0)
    assert len(timeline) == 2
    assert timeline[0] == {
        "slide_number": 1,
        "start": 0.0,
        "end": 2.0,
        "confidence": 0.9,
        "last_frame_image_path": "b.png",  # last event in the run, not the first
    }
    assert timeline[1]["start"] == 2.0 and timeline[1]["end"] == 10.0
    assert timeline[1]["last_frame_image_path"] == "c.png"


def test_collapse_falls_back_to_last_event_timestamp_without_duration():
    matches = [{"timestamp": 5.0, "slide_number": 1, "score": 0.9, "frame_image_path": "a.png"}]
    timeline, notes = m4.collapse_to_timeline(matches, video_duration=None)
    assert timeline[0]["end"] == 5.0
    assert any("video end is unknown" in n or "true video end" in n for n in notes)


# --- frame_hash / hamming_distance (OCR dedup pre-pass) ---------------------


def _make_image(tmp_path, name, fill):
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", (64, 48), color=fill).save(path)
    return path


def test_hamming_distance_identical_hashes_is_zero():
    assert m4.hamming_distance(0b1010, 0b1010) == 0


def test_hamming_distance_counts_differing_bits():
    assert m4.hamming_distance(0b0000, 0b1011) == 3


def test_frame_hash_identical_images_have_zero_distance(tmp_path):
    a = _make_image(tmp_path, "a.png", (200, 200, 200))
    b = _make_image(tmp_path, "b.png", (200, 200, 200))
    assert m4.hamming_distance(m4.frame_hash(a), m4.frame_hash(b)) == 0


def test_frame_hash_a_solid_image_has_no_gradient_bits(tmp_path):
    # a perfectly flat image has no left>right pixel transitions anywhere
    a = _make_image(tmp_path, "flat.png", (128, 128, 128))
    assert m4.frame_hash(a) == 0


def test_frame_hash_distinguishes_very_different_images(tmp_path):
    # half-black-half-white vs. a checkerboard-ish gradient should not
    # collide -- sanity check that the hash isn't degenerate
    from PIL import Image

    a_path = tmp_path / "a.png"
    b_path = tmp_path / "b.png"
    img_a = Image.new("L", (64, 48), color=0)
    for x in range(32, 64):
        for y in range(48):
            img_a.putpixel((x, y), 255)
    img_a.save(a_path)
    img_b = Image.new("L", (64, 48), color=255)
    for x in range(32, 64):
        for y in range(48):
            img_b.putpixel((x, y), 0)
    img_b.save(b_path)
    dist = m4.hamming_distance(m4.frame_hash(a_path), m4.frame_hash(b_path))
    assert dist > m4.DHASH_DEDUP_THRESHOLD
