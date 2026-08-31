"""Golden-master tests (Phase 1, extended in Phase 3, of the architectural
cleanup plan): pin each stage's exact output artifact given fixed,
synthetic inputs.

Stages 2, 4, 5 were reachable from Phase 1 (no external calls, or already
separated from the pure combination logic that does the real work). Stage
6 and stage 7's --topic-index path were unreachable until the LlmClient
port (Phase 3) gave them a fake to inject -- see tests/fakes.py and
tests/test_llm_client_port.py. Stages 0, 1, 3 and 8 still need their own
port before they can be driven hermetically.

Expected artifacts live in tests/golden/expected/ and are committed. If a
golden test goes red, that means real behaviour changed: read the diff,
decide whether it's correct, and only then regenerate the expected file --
never to make a red test green without understanding why it went red.
"""

import json
from pathlib import Path

from conftest import load_stage
from fakes import FakeDocConverter, FakeFrameReader, FakeHtmlToPdf, FakeLlmClient, FakeOcr, llm_response
from notely.pipeline import slides as s2
from notely.ports import SampledFrame
from pdf_fixtures import make_pdf_bytes

EXPECTED_DIR = Path(__file__).parent / "golden" / "expected"


def _expected_json(name):
    return json.loads((EXPECTED_DIR / name).read_text())


def _expected_text(name):
    return (EXPECTED_DIR / name).read_text()


# --- Stage 2: slide text extraction (PDF path) ------------------------------


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


def test_stage02_extract_from_pptx_golden(tmp_path):
    # Previously zero test coverage at all (not just "unreachable without a
    # port" -- extract_from_pptx had no test before this). Promoted
    # straight to golden-mastered by the DocConverter port (Phase 3):
    # FakeDocConverter writes a real, parseable synthetic PDF (reusing
    # make_pdf_bytes) in place of a real soffice conversion, so
    # render_pdf_to_images still runs for real against real pypdfium2.
    from pptx import Presentation

    s2.PROJECT_ROOT = tmp_path

    prs = Presentation()
    layout = prs.slide_layouts[1]  # "Title and Content"
    slide1 = prs.slides.add_slide(layout)
    slide1.shapes.title.text = "Introduction"
    slide1.placeholders[1].text_frame.text = "Overview of the topic today"

    slide2 = prs.slides.add_slide(layout)
    slide2.shapes.title.text = "Summary"
    slide2.placeholders[1].text_frame.text = "Key takeaways"
    slide2.notes_slide.notes_text_frame.text = "Remember to mention the exam date"

    pptx_path = tmp_path / "deck.pptx"
    prs.save(str(pptx_path))

    image_dir = tmp_path / "images"
    pdf_bytes = make_pdf_bytes(["Introduction\nOverview of the topic today", "Summary\nKey takeaways"])
    fake_converter = FakeDocConverter(pdf_bytes=pdf_bytes)

    slides = s2.extract_from_pptx(pptx_path, image_dir, doc_converter=fake_converter)

    assert fake_converter.calls == [(str(pptx_path), str(image_dir))]
    for i, slide in enumerate(slides, start=1):
        assert slide.pop("image_path") == f"images/slide_{i:03d}.png"

    assert slides == _expected_json("stage02_pptx_slides.json")


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


def test_stage04_process_lecture_end_to_end_with_fake_ocr(tmp_path, monkeypatch):
    # Promoted from "pure pipeline only" (above) to genuinely end-to-end by
    # the Ocr port (Phase 3): process_lecture itself -- file I/O, frame
    # hashing (real PIL, not the hand-picked hashes above), and the full
    # OCR -> TF-IDF -> match -> collapse -> needs_review -> examples chain
    # -- is now driven entirely by fakes. get_video_duration() still calls
    # real ffprobe, but only if a video file exists; with none present it
    # returns None before ever shelling out, staying hermetic without
    # needing the MediaProbe port (Phase 3, port 3) yet.
    #
    # This is a smoke test (does the wiring work end-to-end, sane output),
    # not a byte-exact pin like the hand-picked-hash pipeline test above --
    # that test already pins the combination logic precisely; this one's
    # job is proving process_lecture's own OCR loop and file I/O reach it.
    from PIL import Image

    monkeypatch.setattr(s4, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(s4, "FRAME_EVENTS_DIR", tmp_path / "output" / "frame_events")
    monkeypatch.setattr(s4, "SLIDES_EXTRACTED_DIR", tmp_path / "output" / "slides_extracted")
    monkeypatch.setattr(s4, "OUTPUT_DIR", tmp_path / "output" / "slide_timelines")
    monkeypatch.setattr(s4, "INPUT_VIDEOS_DIR", tmp_path / "input" / "videos")  # empty -> duration=None
    s4.FRAME_EVENTS_DIR.mkdir(parents=True)
    s4.SLIDES_EXTRACTED_DIR.mkdir(parents=True)

    frames_dir = s4.FRAME_EVENTS_DIR / "lecture01_frames"
    frames_dir.mkdir(parents=True)
    # Half-black-half-white, flipped per frame -- gives each frame a real,
    # distinguishing dHash (a flat solid color hashes to 0 for every frame,
    # per test_matcher.py's own note, which would wrongly trigger OCR-dedup
    # between every frame here).
    for name, flip in [("frame_000.png", False), ("frame_001.png", True), ("frame_002.png", False)]:
        img = Image.new("L", (64, 48), color=0 if not flip else 255)
        for x in range(32, 64):
            for y in range(48):
                img.putpixel((x, y), 255 if not flip else 0)
        img.save(frames_dir / name)

    rel_paths = [f"output/frame_events/lecture01_frames/frame_{i:03d}.png" for i in range(3)]
    events = [{"timestamp": float(i * 5), "frame_image_path": rel} for i, rel in enumerate(rel_paths)]
    (s4.FRAME_EVENTS_DIR / "lecture01.json").write_text(json.dumps(events))

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
            "title": "Summary",
            "body_text": "Key takeaways",
            "notes_text": "",
            "image_path": "",
        },
    ]
    (s4.SLIDES_EXTRACTED_DIR / "lecture01.json").write_text(json.dumps(slides))

    fake_ocr = FakeOcr(
        texts={
            str(tmp_path / rel_paths[0]): "Introduction Overview",
            str(tmp_path / rel_paths[1]): "Summary Key takeaways",
            str(tmp_path / rel_paths[2]): "Summary Key takeaways",
        }
    )

    ok = s4.process_lecture(
        "lecture01",
        margin=s4.DEFAULT_BACKWARD_JUMP_MARGIN,
        confidence_threshold=s4.DEFAULT_CONFIDENCE_THRESHOLD,
        force=True,
        ocr_lang="eng",
        ocr=fake_ocr,
    )
    assert ok is True

    # every event was actually routed through the fake, in the requested language
    assert fake_ocr.calls == [(str(tmp_path / rel), "eng") for rel in rel_paths]

    timeline_data = json.loads((s4.OUTPUT_DIR / "lecture01.json").read_text())
    assert [entry["slide_number"] for entry in timeline_data["timeline"]] == [1, 2]
    assert (s4.OUTPUT_DIR / "lecture01_needs_review.json").exists()
    assert (s4.OUTPUT_DIR / "lecture01_examples.json").exists()


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


def test_stage07_assemble_guide_with_topic_index_golden(tmp_path, monkeypatch):
    # Promoted from unreachable to golden-masterable by the LlmClient port
    # (Phase 3): generate_topic_index's real anthropic.Anthropic() client is
    # never constructed when a fake is injected via assemble_guide's
    # llm_client parameter, so this needs no ANTHROPIC_API_KEY and makes no
    # network call.
    monkeypatch.setattr(s7, "get_project_root", lambda: tmp_path)
    notes_dir = tmp_path / "output" / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "lecture01.md").write_text("# lecture01\n\n## Introduction\n\n- point one\n")
    (notes_dir / "lecture02.md").write_text("## Worked Example\n\n- point two\n")

    fake = FakeLlmClient(
        responses=[
            llm_response(
                "# Topic Index\n\n- Introduction, covered in [lecture01](#lecture01), "
                "is revisited in [lecture02](#lecture02).",
                input_tokens=500,
                output_tokens=40,
            ),
        ]
    )

    assert s7.assemble_guide(force=True, topic_index=True, llm_client=fake) is True
    assert len(fake.calls) == 1

    guide = (tmp_path / "output" / "study_guide.md").read_text()
    assert guide == _expected_text("stage07_study_guide_with_topic_index.md")


# --- Stage 6: note generation ------------------------------------------------

s6 = load_stage("06_generate_notes.py")


def test_stage06_process_lecture_golden(tmp_path, monkeypatch):
    # Promoted from unreachable to golden-masterable by the LlmClient port
    # (Phase 3). NOTES_CONCURRENCY=1 makes the per-slide ThreadPoolExecutor
    # calls run in submission order, so FakeLlmClient's simple
    # responses.pop(0) queue lines up deterministically with the slides
    # list -- with concurrency > 1 the response each slide receives would
    # race. Worked-example detection stays off (its own confirmation calls
    # are a separate, not-yet-pinned concern).
    for var in ("NOTES_DETECT_EXAMPLES", "NOTES_SEND_FRAME_IMAGE", "NOTES_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NOTES_CONCURRENCY", "1")
    monkeypatch.setattr(s6, "INPUT_SEGMENTED_DIR", tmp_path / "output" / "segmented_transcripts")
    monkeypatch.setattr(s6, "OUTPUT_NOTES_DIR", tmp_path / "output" / "notes")
    s6.INPUT_SEGMENTED_DIR.mkdir(parents=True)

    slides = [
        {
            "slide_number": 1,
            "slide_text": "Introduction\nOverview of the topic today",
            "notes_text": "",
            "transcript_text": "Welcome to the lecture.",
            "start": 0.0,
            "end": 10.0,
            "merged_from": [],
            "frame_image_path": None,
            "example_candidates": [],
        },
        {
            "slide_number": 2,
            "slide_text": "Summary\nKey takeaways",
            "notes_text": "",
            "transcript_text": "That's all for today.",
            "start": 10.0,
            "end": 20.0,
            "merged_from": [],
            "frame_image_path": None,
            "example_candidates": [],
        },
    ]
    (s6.INPUT_SEGMENTED_DIR / "lecture01.json").write_text(json.dumps(slides))

    fake = FakeLlmClient(
        responses=[
            llm_response(
                "## Slide 1\n- Overview of the topic today\n\n**Professor's notes:** Welcomed the class.",
                input_tokens=120,
                output_tokens=40,
            ),
            llm_response(
                "## Slide 2\n- Key takeaways\n\n**Professor's notes:** Wrapped up the lecture.",
                input_tokens=110,
                output_tokens=35,
            ),
            llm_response(
                "## Pregled predavanja\n- Covered the introduction and summary.",
                input_tokens=200,
                output_tokens=20,
            ),
        ]
    )

    assert s6.process_lecture(fake, "lecture01", force=True) is True
    assert len(fake.calls) == 3

    notes = (s6.OUTPUT_NOTES_DIR / "lecture01.md").read_text()
    assert notes == _expected_text("stage06_notes.md")


# --- Stage 8: PDF export -----------------------------------------------------

s8 = load_stage("08_export_pdf.py")


def test_stage08_export_pdf_golden(tmp_path):
    # Promoted from zero coverage (not just "unreachable without a port")
    # to golden-mastered by the HtmlToPdf port (Phase 3). Asserts on
    # structural substrings of the rendered HTML rather than an exact
    # committed fixture: the image-path rewrite embeds an absolute tmp_path,
    # which isn't stable across test runs.
    md_path = tmp_path / "study_guide.md"
    md_path.write_text(
        "# lecture01\n\n"
        "## Introduction\n\n"
        "- The formula is $E = mc^2$\n"
        "- A display equation:\n\n"
        "$$\\int_0^1 x^2 dx = \\frac{1}{3}$$\n\n"
        '<img src="../slides_extracted/lecture01_images/slide_001.png">\n'
    )
    pdf_path = tmp_path / "study_guide.pdf"

    fake = FakeHtmlToPdf(pdf_bytes=b"%PDF-1.4 fake\n%%EOF")
    s8.export_pdf(md_path, pdf_path, html_to_pdf=fake)

    assert len(fake.calls) == 1
    html_uri, rendered_pdf_path = fake.calls[0]
    assert rendered_pdf_path == str(pdf_path)

    html = fake.rendered_html[0]
    assert "<h1>lecture01</h1>" in html
    # math passed through untouched for MathJax, not mangled by the
    # markdown parser (e.g. the underscore in \frac{1}{3} surviving)
    assert "$E = mc^2$" in html
    assert "$$\\int_0^1 x^2 dx = \\frac{1}{3}$$" in html
    # relative image path rewritten to an absolute one Chrome can resolve
    assert 'src="../slides_extracted/' not in html
    assert html.count(f'src="{tmp_path.parent}/slides_extracted/lecture01_images/slide_001.png"') == 1

    assert pdf_path.read_bytes() == b"%PDF-1.4 fake\n%%EOF"


# --- Stage 3: slide-change detection -----------------------------------------

s3 = load_stage("03_detect_slide_changes.py")


def test_stage03_process_lecture_golden(tmp_path, monkeypatch):
    # Previously zero test coverage at all (not just "unreachable without a
    # port"). Promoted straight to golden-mastered by the FrameReader port
    # (Phase 3, the last of the 9): FakeFrameReader hands process_lecture
    # small synthetic numpy frames -- no real video file, no opencv video
    # decoding -- while cv2's own (real) resize/cvtColor/imwrite still run
    # for real on those frames, same as frame_hash's real-PIL precedent in
    # stage 4's tests.
    import numpy as np

    monkeypatch.setattr(s3, "INPUT_VIDEOS_DIR", tmp_path / "input" / "videos")
    monkeypatch.setattr(s3, "OUTPUT_DIR", tmp_path / "output" / "frame_events")
    monkeypatch.setattr(s3, "PROJECT_ROOT", tmp_path)
    video_dir = tmp_path / "input" / "videos"
    video_dir.mkdir(parents=True)
    # process_lecture only checks this file exists -- FakeFrameReader never
    # actually opens it.
    (video_dir / "lecture01.mp4").write_bytes(b"placeholder")

    frames = [
        SampledFrame(frame_idx=0, timestamp=0.0, frame=np.full((48, 64, 3), 50, dtype=np.uint8)),
        # identical color -- no change event
        SampledFrame(frame_idx=45, timestamp=1.5, frame=np.full((48, 64, 3), 50, dtype=np.uint8)),
        # different color -- a change event
        SampledFrame(frame_idx=90, timestamp=3.0, frame=np.full((48, 64, 3), 200, dtype=np.uint8)),
    ]
    fake_reader = FakeFrameReader(frames=frames)

    ok = s3.process_lecture(
        "lecture01", interval=1.5, threshold=0.02, crop_str=None, force=True, frame_reader=fake_reader
    )

    assert ok is True
    assert fake_reader.calls == [(str(video_dir / "lecture01.mp4"), 1.5)]

    events = json.loads((s3.OUTPUT_DIR / "lecture01.json").read_text())
    # first frame always saved, second (identical) frame is not, third
    # (different color) is -- exactly 2 events
    assert [e["timestamp"] for e in events] == [0.0, 3.0]
    for i, event in enumerate(events):
        image_path = tmp_path / event["frame_image_path"]
        assert image_path.exists() and image_path.stat().st_size > 0
        assert event["frame_image_path"] == f"output/frame_events/lecture01_frames/event_{i:03d}.png"
