"""scripts/05_segment_transcript.py::build_canonical_slide_map -- the
duplicate-slide canonicalization that lets pool-mode decks (which repeat
material across merged source decks) collapse to one timeline entry per
truly-unique slide instead of smearing one lecture across several copies
of the same slide."""

from conftest import load_stage

m5 = load_stage("05_segment_transcript.py")


def test_distinct_slides_map_to_themselves():
    slides = [
        {"slide_number": 1, "title": "Uvod", "body_text": "prvi slajd"},
        {"slide_number": 2, "title": "CORDIC", "body_text": "drugi slajd"},
    ]
    canon = m5.build_canonical_slide_map(slides)
    assert canon == {1: 1, 2: 2}


def test_duplicate_text_maps_to_first_occurrence():
    slides = [
        {"slide_number": 5, "title": "Naslov", "body_text": "isti tekst"},
        {"slide_number": 5, "title": "drugo", "body_text": "razlicito"},
        {"slide_number": 130, "title": "Naslov", "body_text": "isti tekst"},  # dup of #5
    ]
    canon = m5.build_canonical_slide_map(slides)
    assert canon[130] == 5
    assert canon[5] == 5


def test_dedup_is_case_and_whitespace_insensitive():
    slides = [
        {"slide_number": 1, "title": "Naslov", "body_text": "Tekst   ovde"},
        {"slide_number": 2, "title": "NASLOV", "body_text": "tekst ovde"},
    ]
    canon = m5.build_canonical_slide_map(slides)
    assert canon[2] == 1


def test_missing_title_or_body_treated_as_empty_string():
    # shouldn't raise on slides missing one of the fields (e.g. a
    # title-only or body-only slide from extraction)
    slides = [
        {"slide_number": 1, "body_text": "only body"},
        {"slide_number": 2, "title": "only title"},
    ]
    canon = m5.build_canonical_slide_map(slides)
    assert canon == {1: 1, 2: 2}


# --- frame_image_path propagation (consolidate_by_slide_number) -----------
# The video frame carrying any live on-slide annotation, tracked so stage 6
# can (opt-in) show it to Claude and embed it in the note -- see
# DOCUMENTATION.md / TODO.md for the full feature.

def test_frame_image_path_carried_through_single_run():
    timeline = [
        {"slide_number": 1, "start": 0.0, "end": 5.0, "last_frame_image_path": "event_000.png"},
    ]
    consolidated = m5.consolidate_by_slide_number(timeline, run_transcripts={}, removed=set(), merges={})
    assert consolidated[0]["frame_image_path"] == "event_000.png"


def test_frame_image_path_picks_chronologically_last_on_revisit():
    # slide 3 shown, then revisited later (a legitimate stage-4 backward
    # jump) -- the second visit's frame is the one that should win, since
    # it's the most complete state of any annotations added across both
    # visits.
    timeline = [
        {"slide_number": 3, "start": 0.0, "end": 5.0, "last_frame_image_path": "event_001.png"},
        {"slide_number": 4, "start": 5.0, "end": 10.0, "last_frame_image_path": "event_002.png"},
        {"slide_number": 3, "start": 10.0, "end": 15.0, "last_frame_image_path": "event_003.png"},
    ]
    consolidated = m5.consolidate_by_slide_number(timeline, run_transcripts={}, removed=set(), merges={})
    slide3 = next(e for e in consolidated if e["slide_number"] == 3)
    assert slide3["frame_image_path"] == "event_003.png"


def test_frame_image_path_none_when_missing_from_timeline():
    # timelines produced before stage 4 started tracking this field
    # shouldn't crash stage 5 -- just no frame to offer downstream.
    timeline = [{"slide_number": 1, "start": 0.0, "end": 5.0}]
    consolidated = m5.consolidate_by_slide_number(timeline, run_transcripts={}, removed=set(), merges={})
    assert consolidated[0]["frame_image_path"] is None


def test_frame_image_path_skips_removed_runs():
    # a run merged away (short dwell) shouldn't contribute its frame over
    # a real surviving run's frame
    timeline = [
        {"slide_number": 1, "start": 0.0, "end": 1.0, "last_frame_image_path": "short_dwell.png"},
        {"slide_number": 1, "start": 1.0, "end": 10.0, "last_frame_image_path": "real.png"},
    ]
    # simulate index 0 having been merged away by merge_short_dwell_runs
    consolidated = m5.consolidate_by_slide_number(timeline, run_transcripts={}, removed={0}, merges={1: [1]})
    assert consolidated[0]["frame_image_path"] == "real.png"
