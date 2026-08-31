"""Worked-example extraction feature: stage 4 candidate detection, stage 5
transcript-cue scoring, and stage 6 verdict parsing / markdown helpers. See
CLAUDE.md's stage [4]/[6] sections and DOCUMENTATION.md for the full feature.
No network calls anywhere in this file."""

import base64
import io

from conftest import load_stage
from notely.pipeline import examples as m6

m4 = load_stage("04_match_frames_to_slides.py")
m5 = load_stage("05_segment_transcript.py")


# --- stage 4: group_runs -----------------------------------------------------


def test_group_runs_groups_consecutive_same_slide():
    matches = [
        {"slide_number": 1, "timestamp": 0.0},
        {"slide_number": 1, "timestamp": 1.0},
        {"slide_number": 2, "timestamp": 2.0},
        {"slide_number": 1, "timestamp": 3.0},  # revisit -> new run, not merged with the first
    ]
    runs = m4.group_runs(matches)
    assert [len(r) for r in runs] == [2, 1, 1]
    assert [r[0]["slide_number"] for r in runs] == [1, 2, 1]


def test_group_runs_empty_input():
    assert m4.group_runs([]) == []


# --- stage 4: detect_example_candidates -------------------------------------


def _match(event_index, slide_number, score, timestamp=None):
    return {
        "event_index": event_index,
        "timestamp": timestamp if timestamp is not None else float(event_index),
        "frame_image_path": f"event_{event_index:03d}.png",
        "slide_number": slide_number,
        "score": score,
        "ocr_excerpt": "",
    }


def test_detect_examples_empty_matches_returns_empty():
    assert m4.detect_example_candidates([], [], {}) == []


def test_detect_examples_low_score_frame_is_whiteboard():
    matches = [_match(0, slide_number=1, score=0.01)]
    slides = [{"slide_number": 1, "title": "Uvod", "body_text": "obican tekst"}]
    hashes = {0: 0}
    candidates = m4.detect_example_candidates(matches, slides, hashes, score_max=0.12)
    assert len(candidates) == 1
    assert candidates[0]["kind"] == "whiteboard"
    assert candidates[0]["event_index"] == 0


def test_detect_examples_ink_accumulation_flags_annotated_slide():
    # Same slide, same (high) match score both times -- only the frame whose
    # dHash has drifted far enough from the run's first frame should flag.
    matches = [
        _match(0, slide_number=1, score=0.5),
        _match(1, slide_number=1, score=0.5),
    ]
    slides = [{"slide_number": 1, "title": "Uvod", "body_text": "obican tekst"}]
    hashes = {0: 0b0000, 1: 0b0111}  # hamming distance 3 from the run's first frame
    candidates = m4.detect_example_candidates(matches, slides, hashes, score_max=0.12, ink_delta=3)
    assert len(candidates) == 1
    assert candidates[0]["event_index"] == 1
    assert candidates[0]["kind"] == "annotated_slide"
    assert candidates[0]["ink_delta"] == 3


def test_detect_examples_matches_example_slide_title():
    matches = [_match(0, slide_number=2, score=0.5)]
    slides = [{"slide_number": 2, "title": "Primer 3", "body_text": ""}]
    hashes = {0: 0}
    candidates = m4.detect_example_candidates(matches, slides, hashes, score_max=0.12, ink_delta=12)
    assert len(candidates) == 1
    assert candidates[0]["kind"] == "example_slide"


def test_detect_examples_ink_drift_without_text_overlap_is_not_annotated():
    # Same failure mode found on real pooled-deck footage: the sequential-
    # order matcher left two genuinely DIFFERENT, unrelated slides under one
    # slide_number/run. dHash drift alone can't tell that apart from real
    # ink -- the OCR text must still substantially overlap for it to count.
    matches = [
        _match(0, slide_number=1, score=0.5),
        _match(1, slide_number=1, score=0.5),
    ]
    matches[0]["ocr_excerpt"] = "MIB Sluzi za nadgledanje mreznih uredjaja"
    matches[1]["ocr_excerpt"] = "RIPE Atlas projekat Nastao u jesen 2010"
    slides = [{"slide_number": 1, "title": "Uvod", "body_text": "obican tekst"}]
    hashes = {0: 0b0000, 1: 0b0111}  # hamming distance 3, well above ink_delta
    candidates = m4.detect_example_candidates(
        matches, slides, hashes, score_max=0.12, ink_delta=3, ink_text_overlap_min=0.5
    )
    assert candidates == []


def test_detect_examples_animation_build_reveal_is_not_annotated():
    # Same slide, same OCR-overlap-passing text, big dHash drift -- but
    # every word visible in the candidate frame is already in the deck's
    # own extracted text (a PowerPoint animation just revealed more of it).
    # No real ink was added, so this must NOT be flagged.
    matches = [
        _match(0, slide_number=1, score=0.5),
        _match(1, slide_number=1, score=0.5),
    ]
    matches[0]["ocr_excerpt"] = "RDBMS MIB databases installed"
    matches[1]["ocr_excerpt"] = "RDBMS MIB databases installed actively opened databases"
    slides = [
        {
            "slide_number": 1,
            "title": "Uvod",
            "body_text": "RDBMS MIB databases installed actively opened databases configuration parameters",
        }
    ]
    hashes = {0: 0b0000, 1: 0b0111}  # hamming distance 3, well above ink_delta
    candidates = m4.detect_example_candidates(
        matches,
        slides,
        hashes,
        score_max=0.12,
        ink_delta=3,
        ink_text_overlap_min=0.5,
        ink_novel_word_min=0.2,
    )
    assert candidates == []


def test_detect_examples_ink_drift_with_text_overlap_is_annotated():
    # Same scenario, but this time the candidate frame's OCR text still
    # contains most of the run's first frame's words -- consistent with
    # real ink added on top of the same printed slide.
    matches = [
        _match(0, slide_number=1, score=0.5),
        _match(1, slide_number=1, score=0.5),
    ]
    matches[0]["ocr_excerpt"] = "MIB Sluzi za nadgledanje mreznih uredjaja"
    matches[1]["ocr_excerpt"] = "MIB Sluzi za nadgledanje mreznih uredjaja x=5"
    slides = [{"slide_number": 1, "title": "Uvod", "body_text": "obican tekst"}]
    hashes = {0: 0b0000, 1: 0b0111}
    candidates = m4.detect_example_candidates(
        matches, slides, hashes, score_max=0.12, ink_delta=3, ink_text_overlap_min=0.5
    )
    assert len(candidates) == 1
    assert candidates[0]["kind"] == "annotated_slide"


def test_ocr_text_overlap_full_overlap_is_one():
    assert m4.ocr_text_overlap("hello world", "hello world and more") == 1.0


def test_ocr_text_overlap_no_shared_words_is_zero():
    assert m4.ocr_text_overlap("hello world", "goodbye moon") == 0.0


def test_ocr_text_overlap_partial_overlap():
    assert m4.ocr_text_overlap("a b c d", "a b x y") == 0.5


def test_ocr_text_overlap_empty_reference_returns_one():
    # Nothing to check containment of (e.g. a diagram-only slide with no
    # OCR'd text) -- treat as "can't rule it out", not "no overlap".
    assert m4.ocr_text_overlap("", "anything") == 1.0


def test_ocr_text_overlap_is_case_and_punctuation_insensitive():
    assert m4.ocr_text_overlap("Hello, World!", "hello world") == 1.0


# --- stage 4: ocr_novel_word_ratio ------------------------------------------


def test_ocr_novel_word_ratio_all_words_already_in_deck_text_is_zero():
    # Calibrated against a real false positive: a dense slide revealed by a
    # PowerPoint animation build. Every word visible in the frame is
    # somewhere in the deck's own (full, final) extracted text, so nothing
    # here is "novel" even though the frame differs a lot, pixel-wise, from
    # an earlier build step.
    deck_text = "RDBMS MIB RFC 1697 databases installed on a host system"
    frame_ocr = "RDBMS MIB RFC 1697 databases installed on a"
    assert m4.ocr_novel_word_ratio(deck_text, frame_ocr) == 0.0


def test_ocr_novel_word_ratio_handwritten_words_not_in_deck_are_novel():
    deck_text = "Formula for signal to noise ratio"
    frame_ocr = "Formula for signal to noise ratio x equals five"
    # "x", "equals", "five" are novel (not anywhere in the deck's own text);
    # the other 6 words are already printed on the slide.
    assert m4.ocr_novel_word_ratio(deck_text, frame_ocr) == 3 / 9


def test_ocr_novel_word_ratio_empty_frame_ocr_returns_one():
    # Nothing OCR'd at all (e.g. a hand-drawn diagram with no text, or a
    # failed OCR call) -- can't rule out real annotation, so don't reject
    # on this signal alone; defer to ink_delta / ocr_text_overlap instead.
    assert m4.ocr_novel_word_ratio("some deck text", "") == 1.0


def test_detect_examples_ordinary_slide_is_not_a_candidate():
    matches = [_match(0, slide_number=1, score=0.5)]
    slides = [{"slide_number": 1, "title": "Uvod", "body_text": "obican tekst"}]
    hashes = {0: 0}
    assert m4.detect_example_candidates(matches, slides, hashes, score_max=0.12, ink_delta=12) == []


def test_detect_examples_collapses_consecutive_same_kind_to_last():
    # Three consecutive whiteboard-scored frames in one run should collapse
    # to a single candidate -- the last (most complete) one.
    matches = [_match(i, slide_number=1, score=0.01) for i in range(3)]
    slides = [{"slide_number": 1, "title": "Uvod", "body_text": "obican tekst"}]
    hashes = {0: 0, 1: 0, 2: 0}
    candidates = m4.detect_example_candidates(matches, slides, hashes, score_max=0.12)
    assert len(candidates) == 1
    assert candidates[0]["event_index"] == 2


def test_detect_examples_does_not_collapse_across_different_kinds():
    # event 0: low score -> whiteboard. event 1: high score but example-slide
    # title -> example_slide. Different kinds in the same run must not merge.
    matches = [_match(0, slide_number=1, score=0.01), _match(1, slide_number=1, score=0.5)]
    slides = [{"slide_number": 1, "title": "Primer 1", "body_text": ""}]
    hashes = {0: 0, 1: 0}
    candidates = m4.detect_example_candidates(matches, slides, hashes, score_max=0.12, ink_delta=12)
    assert [c["kind"] for c in candidates] == ["whiteboard", "example_slide"]


# --- stage 5: fold / transcript_context_for / score_example_cues -----------


def test_fold_strips_diacritics_and_lowercases():
    assert m5.fold("Vežbanje") == "vezbanje"


def test_score_example_cues_matches_diacritic_folded_cue():
    context = "Sada radimo Vežbanje broj dva, pa reci mi rezultat."
    hits = m5.score_example_cues(context)
    assert "vezb" in hits


def test_score_example_cues_no_hits_when_nothing_matches():
    assert m5.score_example_cues("Ovo je obicna recenica bez signala.") == []


def test_transcript_context_for_includes_overlapping_segments_only():
    segments = [
        {"start": 0.0, "end": 5.0, "text": "a"},
        {"start": 10.0, "end": 15.0, "text": "b"},
        {"start": 100.0, "end": 105.0, "text": "c"},
    ]
    context = m5.transcript_context_for(12.0, segments, before=20.0, after=40.0)
    assert context == "a b"


def test_transcript_context_for_empty_segments_returns_empty_string():
    assert m5.transcript_context_for(10.0, []) == ""


# --- stage 5: attach_example_candidates -------------------------------------


def test_attach_example_candidates_lands_on_matching_slide_number():
    output = [
        {"slide_number": 1, "windows": [[0.0, 10.0]], "example_candidates": []},
        {"slide_number": 2, "windows": [[10.0, 20.0]], "example_candidates": []},
    ]
    examples_data = [
        {
            "event_index": 0,
            "timestamp": 12.0,
            "frame_image_path": "e.png",
            "slide_number": 2,
            "kind": "whiteboard",
            "score": 0.01,
            "ink_delta": None,
            "ocr_excerpt": "",
        },
    ]
    m5.attach_example_candidates(examples_data, output, canonical={}, transcript_segments=[])
    assert output[0]["example_candidates"] == []
    assert len(output[1]["example_candidates"]) == 1
    assert output[1]["example_candidates"][0]["kind"] == "whiteboard"
    assert "transcript_context" in output[1]["example_candidates"][0]
    assert "cue_hits" in output[1]["example_candidates"][0]


def test_attach_example_candidates_falls_back_to_window_containment():
    # candidate's slide_number was merged away (no surviving output entry
    # for slide 2) -- attach by which entry's window contains the timestamp.
    output = [
        {"slide_number": 1, "windows": [[0.0, 25.0]], "example_candidates": []},
    ]
    examples_data = [
        {
            "event_index": 0,
            "timestamp": 12.0,
            "frame_image_path": "e.png",
            "slide_number": 2,
            "kind": "whiteboard",
            "score": 0.01,
            "ink_delta": None,
            "ocr_excerpt": "",
        },
    ]
    m5.attach_example_candidates(examples_data, output, canonical={}, transcript_segments=[])
    assert len(output[0]["example_candidates"]) == 1


def test_attach_example_candidates_empty_output_is_a_noop():
    output = []
    m5.attach_example_candidates([{"slide_number": 1, "timestamp": 0.0}], output, {}, [])
    assert output == []


def test_attach_example_candidates_no_candidates_still_sets_empty_key():
    output = [{"slide_number": 1, "windows": [[0.0, 5.0]]}]
    m5.attach_example_candidates([], output, {}, [])
    assert output[0]["example_candidates"] == []


# --- stage 6: parse_example_verdict -----------------------------------------


def test_parse_verdict_bare_json():
    text = '{"is_example": true, "kind": "whiteboard", "caption": "x", "confidence": 0.9}'
    verdict = m6.parse_example_verdict(text)
    assert verdict == {"is_example": True, "kind": "whiteboard", "caption": "x", "confidence": 0.9}


def test_parse_verdict_fenced_json():
    text = '```json\n{"is_example": false, "kind": "annotated_slide", "caption": "", "confidence": 0.1}\n```'
    verdict = m6.parse_example_verdict(text)
    assert verdict["is_example"] is False
    assert verdict["kind"] == "annotated_slide"


def test_parse_verdict_prose_wrapped_json():
    text = (
        "Sure, here is my assessment:\n"
        '{"is_example": true, "kind": "example_slide", "caption": "worked derivation", "confidence": 0.8}\n'
        "Let me know if you need anything else."
    )
    verdict = m6.parse_example_verdict(text)
    assert verdict["is_example"] is True
    assert verdict["caption"] == "worked derivation"


def test_parse_verdict_garbage_returns_none():
    assert m6.parse_example_verdict("I cannot determine this from the image.") is None


def test_parse_verdict_empty_string_returns_none():
    assert m6.parse_example_verdict("") is None


def test_parse_verdict_missing_required_field_returns_none():
    assert m6.parse_example_verdict('{"kind": "whiteboard"}') is None


# --- stage 6: fmt_ts / frame_md_path -----------------------------------------


def test_fmt_ts_under_a_minute():
    assert m6.fmt_ts(5) == "0:05"


def test_fmt_ts_minutes_and_seconds():
    assert m6.fmt_ts(65) == "1:05"


def test_fmt_ts_past_an_hour():
    assert m6.fmt_ts(3661) == "1:01:01"


def test_frame_md_path_strips_output_prefix_and_points_up_one_level():
    assert m6.frame_md_path("output/frame_events/lecture01_frames/event_042.png") == (
        "../frame_events/lecture01_frames/event_042.png"
    )


# --- stage 6: rank_example_candidates ---------------------------------------


def test_rank_example_candidates_prefers_cue_hits_then_kind_then_time():
    slide = {}
    late_with_cue = (slide, {"kind": "example_slide", "timestamp": 100.0, "cue_hits": ["primer"]})
    early_whiteboard_no_cue = (slide, {"kind": "whiteboard", "timestamp": 1.0, "cue_hits": []})
    ranked = m6.rank_example_candidates([early_whiteboard_no_cue, late_with_cue])
    assert ranked[0] is late_with_cue  # cue hit outranks kind/time


# --- stage 6: load_frame_image_b64 with an explicit max_dim ----------------


def _make_png(tmp_path, name, size, color=(120, 140, 160)):
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", size, color=color).save(path)
    return path


def test_load_frame_image_respects_explicit_max_dim(tmp_path):
    _make_png(tmp_path, "big.png", (3840, 2160))
    m6.PROJECT_ROOT = tmp_path
    b64 = m6.load_frame_image_b64("big.png", max_dim=1024)
    from PIL import Image

    decoded = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert max(decoded.size) <= 1024
    assert round(decoded.size[0] / decoded.size[1], 3) == round(3840 / 2160, 3)


def test_load_frame_image_default_max_dim_still_1568(tmp_path):
    _make_png(tmp_path, "big.png", (3840, 2160))
    m6.PROJECT_ROOT = tmp_path
    b64 = m6.load_frame_image_b64("big.png")
    from PIL import Image

    decoded = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert max(decoded.size) <= m6.FRAME_IMAGE_MAX_DIM
    assert max(decoded.size) > 1024  # default (1568) is bigger than the example-confirm path's 1024
