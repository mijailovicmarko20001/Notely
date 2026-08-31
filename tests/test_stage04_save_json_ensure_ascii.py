"""Found while surveying scripts/*.py's load_json/save_json variants ahead
of consolidating them into notely/io.py (cleanup plan, Phase 4): stage 4's
save_json was the one holdout that didn't pass ensure_ascii=False to
json.dump, unlike stages 5/6/7's atomic-write helpers (05's save_json,
06/07's _write_json_atomic) -- see D5 (Phase 2) for the exact same
convention being established there. Not a crash like D5 (json.dump's
default ensure_ascii=True doesn't raise), but it means stage 4's own
output files (slide_timelines/<id>.json, _needs_review.json,
_examples.json) silently \\u-escape this course's actual Serbian text
(OCR excerpts, slide titles) instead of writing it as UTF-8, inconsistent
with every other stage's JSON output."""

from notely.pipeline import matching as m4


def test_save_json_writes_non_ascii_text_as_utf8_not_escaped(tmp_path):
    path = tmp_path / "out.json"
    m4.save_json(path, {"ocr_excerpt": "Ovo je čšžđć test"})

    raw = path.read_bytes()
    assert "čšžđć".encode() in raw, raw
    assert b"\\u010d" not in raw  # the escaped form this bug produced
