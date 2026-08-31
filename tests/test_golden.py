"""Golden-master tests (Phase 1 of the architectural cleanup plan): pin each
stage's exact output artifact given fixed, synthetic inputs.

Only stages 2, 4, 5 and 7 are reachable this way today -- their external
calls are either absent (5, and 7's default no-`--topic-index` path) or
already separated from the pure combination logic that does the real work
(2's `extract_from_pdf`/`extract_from_pptx` split; 4's OCR/ffprobe calls sit
in `process_lecture`, one level above the pure matching pipeline exercised
here). Stages 0, 1, 3, 6 and 8 need their port (Phase 3) before they can be
driven hermetically -- see the plan.

Expected artifacts live in tests/golden/expected/ and are committed. If a
golden test goes red, that means real behaviour changed: read the diff,
decide whether it's correct, and only then regenerate the expected file --
never to make a red test green without understanding why it went red.
"""

import json
from pathlib import Path

from conftest import load_stage
from pdf_fixtures import make_pdf_bytes

EXPECTED_DIR = Path(__file__).parent / "golden" / "expected"


def _expected_json(name):
    return json.loads((EXPECTED_DIR / name).read_text())


def _expected_text(name):
    return (EXPECTED_DIR / name).read_text()


# --- Stage 2: slide text extraction (PDF path) ------------------------------

s2 = load_stage("02_extract_slides.py")


def test_stage02_extract_from_pdf_golden(tmp_path):
    s2.PROJECT_ROOT = tmp_path
    pdf_path = tmp_path / "deck.pdf"
    pdf_path.write_bytes(
        make_pdf_bytes(
            [
                "Introduction\nOverview of the topic today",
                "Worked Example\nExample solve 2 plus 2 equals 4 step by step",
                "Summary\nKey takeaways and conclusion",
            ]
        )
    )
    image_dir = tmp_path / "images"

    slides = s2.extract_from_pdf(pdf_path, image_dir)

    # image_path is deterministic (relative to PROJECT_ROOT, no tmp-path
    # leakage) but its PNG bytes aren't pinned here -- pypdfium2's rendering
    # isn't guaranteed byte-stable across versions/platforms, and stage 2's
    # real risk is the title/body split, not pixel-identical rendering.
    for i, slide in enumerate(slides, start=1):
        assert slide.pop("image_path") == f"images/slide_{i:03d}.png"

    assert slides == _expected_json("stage02_slides.json")


# --- Stage 4: frame-to-slide matching (pure combination pipeline) ----------

s4 = load_stage("04_match_frames_to_slides.py")

STAGE4_SLIDES = [
    {
        "slide_number": 1,
        "title": "Introduction",
        "body_text": "Overview of the topic today",
        "notes_text": "",
        "image_path": "slide_001.png",
    },
    {
        "slide_number": 2,
        "title": "Worked Example",
        "body_text": "Example: solve 2 + 2 = 4 step by step",
        "notes_text": "",
        "image_path": "slide_002.png",
    },
    {
        "slide_number": 3,
        "title": "Summary",
        "body_text": "Key takeaways and conclusion",
        "notes_text": "",
        "image_path": "slide_003.png",
    },
]

STAGE4_EVENTS = [
    {
        "timestamp": 0.0,
        "frame_image_path": "frame_000.png",
        "ocr_text": "Introduction Overview of the topic today",
    },
    {
        "timestamp": 5.0,
        "frame_image_path": "frame_001.png",
        "ocr_text": "Introduction Overview of the topic today",
    },
    # OCR came back empty -- e.g. a blank/transitional frame -- exercises
    # the low-confidence-match and "whiteboard" example-candidate paths.
    {"timestamp": 10.0, "frame_image_path": "frame_002.png", "ocr_text": ""},
    {
        "timestamp": 15.0,
        "frame_image_path": "frame_003.png",
        "ocr_text": "Worked Example Example solve 2 + 2 = 4 step by step",
    },
    # Same slide, but with extra words that never appear in the deck's own
    # text for slide 2 -- paired with a hand-picked dHash below, this
    # exercises the "annotated_slide" example-candidate path.
    {
        "timestamp": 20.0,
        "frame_image_path": "frame_004.png",
        "ocr_text": "Worked Example Example solve 2 + 2 = 4 step by step plus scribbled extra notes here",
    },
    {
        "timestamp": 25.0,
        "frame_image_path": "frame_005.png",
        "ocr_text": "Summary Key takeaways and conclusion",
    },
]

# Hand-picked dHashes, not real image renders -- frame_hash() itself is
# already pinned by tests/test_matcher.py. Event 4's hash is maximally far
# (64 bits) from event 3's, everything else in the same run is identical, so
# detect_example_candidates' ink-delta branch fires deterministically.
STAGE4_HASHES = {
    0: 0x0F0F0F0F0F0F0F0F,
    1: 0x0F0F0F0F0F0F0F0F,
    2: 0x0F0F0F0F0F0F0F0F,
    3: 0x0000000000000000,
    4: 0xFFFFFFFFFFFFFFFF,
    5: 0xFFFFFFFFFFFFFFFF,
}


def test_stage04_matching_pipeline_golden():
    # Mirrors process_lecture's combination logic (scripts/04:772-897) minus
    # the two calls that don't have a port yet (ocr_frame -> pytesseract,
    # get_video_duration -> ffprobe): OCR text is supplied directly on the
    # fixture events, and video_duration is a fixed value. Phase 3 adds the
    # Ocr and MediaProbe ports and promotes this to a true end-to-end
    # process_lecture golden master.
    slide_numbers = sorted(s["slide_number"] for s in STAGE4_SLIDES)
    slide_refs = s4.build_slide_reference_texts(STAGE4_SLIDES)
    event_texts = [e["ocr_text"] for e in STAGE4_EVENTS]
    slide_texts = [slide_refs[n] for n in slide_numbers]

    sim_matrix = s4.compute_similarity_matrix(event_texts, slide_texts)
    matches = s4.match_events_to_slides(
        STAGE4_EVENTS,
        slide_numbers,
        sim_matrix,
        margin=s4.DEFAULT_BACKWARD_JUMP_MARGIN,
        stay_margin=s4.DEFAULT_STAY_MARGIN,
        min_forward_score=s4.DEFAULT_MIN_FORWARD_SCORE,
    )
    video_duration = 30.0
    timeline, notes = s4.collapse_to_timeline(matches, video_duration)
    needs_review = s4.build_needs_review(matches, timeline, STAGE4_SLIDES, s4.DEFAULT_CONFIDENCE_THRESHOLD)
    example_candidates = s4.detect_example_candidates(matches, STAGE4_SLIDES, STAGE4_HASHES)

    assert {"timeline": timeline, "notes": notes} == _expected_json("stage04_timeline.json")
    assert needs_review == _expected_json("stage04_needs_review.json")
    assert example_candidates == _expected_json("stage04_examples.json")


# --- Stage 5: transcript segmentation ---------------------------------------

s5 = load_stage("05_segment_transcript.py")


def test_stage05_segment_transcript_golden(tmp_path, monkeypatch):
    monkeypatch.setattr(s5, "get_project_root", lambda: tmp_path)
    (tmp_path / "output" / "slide_timelines").mkdir(parents=True)
    (tmp_path / "output" / "transcripts").mkdir(parents=True)
    (tmp_path / "output" / "slides_extracted").mkdir(parents=True)

    timeline = {
        "timeline": [
            {
                "slide_number": 1,
                "start": 0.0,
                "end": 10.0,
                "confidence": 0.9,
                "last_frame_image_path": "a.png",
            },
            # short dwell (3s < min_dwell=5.0) -- merges into slide 3, its
            # chronologically adjacent neighbor
            {
                "slide_number": 2,
                "start": 10.0,
                "end": 13.0,
                "confidence": 0.8,
                "last_frame_image_path": "b.png",
            },
            {
                "slide_number": 3,
                "start": 13.0,
                "end": 30.0,
                "confidence": 0.85,
                "last_frame_image_path": "c.png",
            },
        ],
        "notes": [],
    }
    (tmp_path / "output" / "slide_timelines" / "lecture01.json").write_text(json.dumps(timeline))

    # Stage 4's optional example-candidates output -- attached to whichever
    # consolidated slide entry the candidate's timestamp falls under, which
    # after the merge above is slide 3, not slide 2.
    examples = {
        "candidates": [
            {
                "event_index": 3,
                "timestamp": 15.0,
                "frame_image_path": "frame_003.png",
                "slide_number": 2,
                "kind": "example_slide",
                "score": 0.42,
                "ink_delta": None,
                "ocr_excerpt": "Worked Example",
            },
        ]
    }
    (tmp_path / "output" / "slide_timelines" / "lecture01_examples.json").write_text(json.dumps(examples))

    transcript = {
        "segments": [
            {"start": 0.0, "end": 9.5, "text": "Welcome to the lecture."},
            {"start": 9.5, "end": 13.5, "text": "Quick aside here."},
            {"start": 13.5, "end": 29.0, "text": "Now let's cover the summary."},
        ]
    }
    (tmp_path / "output" / "transcripts" / "lecture01.json").write_text(json.dumps(transcript))

    slides = [
        {
            "slide_number": 1,
            "title": "Introduction",
            "body_text": "Overview",
            "notes_text": "",
            "image_path": "",
        },
        {
            "slide_number": 2,
            "title": "Worked Example",
            "body_text": "Example: solve 2 + 2 = 4",
            "notes_text": "",
            "image_path": "",
        },
        {
            "slide_number": 3,
            "title": "Summary",
            "body_text": "Key takeaways",
            "notes_text": "",
            "image_path": "",
        },
    ]
    (tmp_path / "output" / "slides_extracted" / "lecture01.json").write_text(json.dumps(slides))

    assert s5.segment_transcript("lecture01", min_dwell=5.0, force=True) is True

    output = json.loads((tmp_path / "output" / "segmented_transcripts" / "lecture01.json").read_text())
    assert output == _expected_json("stage05_segments.json")


# --- Stage 7: assembly (default path, no --topic-index) --------------------

s7 = load_stage("07_assemble.py")


def test_stage07_assemble_guide_golden(tmp_path, monkeypatch):
    monkeypatch.setattr(s7, "get_project_root", lambda: tmp_path)
    notes_dir = tmp_path / "output" / "notes"
    notes_dir.mkdir(parents=True)
    # already has its own "# lecture01" H1 -- assemble_guide must not add a
    # second one (see read_lecture_notes's docstring: a duplicate H1 makes
    # PDF export emit a near-blank page per lecture)
    (notes_dir / "lecture01.md").write_text("# lecture01\n\n## Introduction\n\n- point one\n")
    # missing its own H1 -- assemble_guide must inject one
    (notes_dir / "lecture02.md").write_text("## Worked Example\n\n- point two\n")

    assert s7.assemble_guide(force=True, topic_index=False) is True

    guide = (tmp_path / "output" / "study_guide.md").read_text()
    assert guide == _expected_text("stage07_study_guide.md")
