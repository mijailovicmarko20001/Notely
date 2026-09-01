"""Automatic deck -> visual fallback: when deck matching goes badly enough
that the deck almost certainly wasn't on screen, stage 4 rebuilds the timeline
by visual segmentation instead.

A deckless lecture doesn't fail loudly -- match_events_to_slides always assigns
*a* slide number, just a meaningless one -- so the only available signal is
"almost every match is low-confidence". These tests pin the decision rule and,
more importantly, that a fallback is never silent: it has to be visible in the
artifact a human reads afterwards.
"""

import json

import pytest

from notely.pipeline import matching


def _match(score, index=0):
    return {
        "event_index": index,
        "timestamp": index * 10.0,
        "frame_image_path": f"output/f/{index:03d}.png",
        "ocr_excerpt": "",
        "slide_number": 1,
        "score": score,
        "jumped_backward": False,
        "stayed_over_raw_best": False,
        "in_order_best_slide": 1,
        "in_order_best_score": score,
        "backward_best_slide": None,
        "backward_best_score": None,
    }


def _matches(scores):
    return [_match(s, i) for i, s in enumerate(scores)]


# --- low_confidence_ratio ---------------------------------------------------


def test_ratio_of_empty_matches_is_zero():
    """No evidence of failure is not evidence of failure."""
    assert matching.low_confidence_ratio([], 0.25) == 0.0


def test_ratio_counts_only_scores_below_the_threshold():
    assert matching.low_confidence_ratio(_matches([0.1, 0.1, 0.9, 0.9]), 0.25) == 0.5


def test_ratio_treats_a_score_exactly_at_the_threshold_as_confident():
    """`score < threshold` is the same comparison build_needs_review uses, so
    the two never disagree about which matches are flagged."""
    assert matching.low_confidence_ratio(_matches([0.25, 0.25]), 0.25) == 0.0


def test_ratio_all_low():
    assert matching.low_confidence_ratio(_matches([0.04, 0.06, 0.15]), 0.25) == 1.0


# --- should_fall_back_to_visual ---------------------------------------------


def test_falls_back_when_essentially_everything_is_low_confidence():
    """The measured real case: 445 events, every match far below threshold."""
    assert matching.should_fall_back_to_visual(_matches([0.04] * 10), 0.25, 0.60) is True


def test_does_not_fall_back_on_a_healthy_deck_match():
    assert matching.should_fall_back_to_visual(_matches([0.4] * 10), 0.25, 0.60) is False


def test_boundary_is_inclusive_at_the_threshold():
    """Exactly 60% low-confidence triggers, matching the flag's own wording
    ('this fraction or more')."""
    assert matching.should_fall_back_to_visual(_matches([0.1] * 6 + [0.9] * 4), 0.25, 0.60) is True


def test_just_under_the_threshold_does_not_trigger():
    assert matching.should_fall_back_to_visual(_matches([0.1] * 5 + [0.9] * 5), 0.25, 0.60) is False


@pytest.mark.parametrize("disabled", [0, 0.0, -1])
def test_threshold_of_zero_or_less_disables_the_check(disabled):
    """Set to 0 to keep slide matching no matter how badly it went."""
    assert matching.should_fall_back_to_visual(_matches([0.0] * 10), 0.25, disabled) is False


def test_no_matches_never_falls_back():
    assert matching.should_fall_back_to_visual([], 0.25, 0.60) is False


# --- end to end through process_lecture -------------------------------------


def _stage4_fixture(tmp_path, monkeypatch, ocr_texts):
    """Frame events + a slide deck on disk, with matching's directories
    pointed at tmp_path. `ocr_texts` becomes each frame's OCR result, so a
    test controls entirely whether the deck matches."""
    monkeypatch.setattr(matching, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(matching, "FRAME_EVENTS_DIR", tmp_path / "frame_events")
    monkeypatch.setattr(matching, "SLIDES_EXTRACTED_DIR", tmp_path / "slides_extracted")
    monkeypatch.setattr(matching, "OUTPUT_DIR", tmp_path / "slide_timelines")
    for d in ("frame_events", "slides_extracted", "slide_timelines"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    events = [
        {"timestamp": i * 60.0, "frame_image_path": f"frame_events/f{i:03d}.png"}
        for i in range(len(ocr_texts))
    ]
    (tmp_path / "frame_events" / "lecture01.json").write_text(json.dumps(events))
    (tmp_path / "slides_extracted" / "lecture01.json").write_text(
        json.dumps(
            [
                {"slide_number": 1, "title": "Introduction", "body_text": "overview of the topic"},
                {"slide_number": 2, "title": "Method", "body_text": "the algorithm and its steps"},
            ]
        )
    )
    # Distinct hashes so nothing is deduped away.
    monkeypatch.setattr(matching, "frame_hash", lambda p: int(p.name[1:4]) * 0x11111111)

    class Ocr:
        def image_to_text(self, path, lang):
            return ocr_texts[int(path.name[1:4])]

    return Ocr()


def test_process_lecture_falls_back_and_records_it(tmp_path, monkeypatch):
    """Frames whose text has nothing to do with the deck -- the deckless case."""
    ocr = _stage4_fixture(
        tmp_path,
        monkeypatch,
        ["jupyter pandas dataframe groupby" for _ in range(4)]
        + ["networkx graph edges centrality" for _ in range(4)],
    )

    ok = matching.process_lecture("lecture01", 0.15, 0.25, force=True, ocr=ocr, media_probe=None)

    assert ok is True
    written = json.loads((tmp_path / "slide_timelines" / "lecture01.json").read_text())
    assert written["mode"] == "visual", "should have switched away from deck"
    assert any("AUTOMATIC FALLBACK" in n for n in written["notes"]), (
        "a silent switch is the thing this must never be"
    )


def test_fallback_note_names_the_numbers_that_caused_it(tmp_path, monkeypatch):
    ocr = _stage4_fixture(tmp_path, monkeypatch, ["totally unrelated content here"] * 6)

    matching.process_lecture("lecture01", 0.15, 0.25, force=True, ocr=ocr, media_probe=None)

    note = next(
        n
        for n in json.loads((tmp_path / "slide_timelines" / "lecture01.json").read_text())["notes"]
        if "AUTOMATIC FALLBACK" in n
    )
    assert "confidence threshold" in note
    assert "--mode deck" in note, "must say how to override it"


def test_disabling_the_threshold_keeps_deck_mode(tmp_path, monkeypatch):
    ocr = _stage4_fixture(tmp_path, monkeypatch, ["totally unrelated content here"] * 6)

    matching.process_lecture(
        "lecture01", 0.15, 0.25, force=True, ocr=ocr, media_probe=None, auto_visual_threshold=0
    )

    written = json.loads((tmp_path / "slide_timelines" / "lecture01.json").read_text())
    assert written["mode"] == "deck"
    assert not any("AUTOMATIC FALLBACK" in n for n in written["notes"])


def test_explicit_visual_mode_is_not_reported_as_a_fallback(tmp_path, monkeypatch):
    """Asking for visual mode directly is not the same event as being moved
    there automatically, and shouldn't look like it in the artifact."""
    ocr = _stage4_fixture(tmp_path, monkeypatch, ["some screen text here"] * 6)

    matching.process_lecture(
        "lecture01", 0.15, 0.25, force=True, ocr=ocr, media_probe=None, mode="visual"
    )

    written = json.loads((tmp_path / "slide_timelines" / "lecture01.json").read_text())
    assert written["mode"] == "visual"
    assert not any("AUTOMATIC FALLBACK" in n for n in written["notes"])


def test_fallback_empties_needs_review(tmp_path, monkeypatch):
    """The per-frame slide matches no longer describe the written timeline, so
    keeping them in needs_review would invite corrections against windows that
    no longer exist."""
    ocr = _stage4_fixture(tmp_path, monkeypatch, ["totally unrelated content here"] * 6)

    matching.process_lecture("lecture01", 0.15, 0.25, force=True, ocr=ocr, media_probe=None)

    review = json.loads((tmp_path / "slide_timelines" / "lecture01_needs_review.json").read_text())
    assert review["low_confidence_matches"] == []
    assert review["backward_jumps"] == []
