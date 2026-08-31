"""Found in the same survey as the stage 4 sibling
(test_stage04_save_json_ensure_ascii.py): stage 2's process_lecture writes
output/slides_extracted/<id>.json via json.dumps(slides, indent=2) with no
ensure_ascii=False, unlike stages 1/5/6/7's writers. Slide titles/body
text extracted from a real deck are exactly the kind of content that
regularly contains this course's Serbian diacritics, so this one is a
real (if silent, non-crashing) bug, not just cosmetic inconsistency.

extract_from_pdf is stubbed here rather than fed a non-ASCII PDF fixture:
tests/pdf_fixtures.make_pdf_bytes's own docstring notes it only supports
plain ASCII content (its hand-rolled PDF content stream doesn't encode
Unicode text reliably), which would make a real diacritic round-trip
through it a test of the fixture's limitations, not of process_lecture's
JSON write."""

from notely.pipeline import slides as s2


def test_process_lecture_writes_non_ascii_slide_text_as_utf8_not_escaped(tmp_path, monkeypatch):
    monkeypatch.setattr(s2, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(s2, "SLIDES_DIR", tmp_path / "input" / "slides")
    monkeypatch.setattr(s2, "OUTPUT_DIR", tmp_path / "output" / "slides_extracted")
    s2.SLIDES_DIR.mkdir(parents=True)
    (s2.SLIDES_DIR / "lecture01.pdf").write_bytes(b"%PDF-1.4 placeholder\n%%EOF")

    monkeypatch.setattr(
        s2,
        "extract_from_pdf",
        lambda pdf_path, image_dir: [
            {
                "slide_number": 1,
                "title": "Ovo je čšžđć test",
                "body_text": "Body",
                "notes_text": "",
                "image_path": "",
            }
        ],
    )

    assert s2.process_lecture("lecture01", force=True) is True

    raw = (s2.OUTPUT_DIR / "lecture01.json").read_bytes()
    assert "čšžđć".encode() in raw, raw
    assert b"\\u010d" not in raw  # the escaped form this bug produced
