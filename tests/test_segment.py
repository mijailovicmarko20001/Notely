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
