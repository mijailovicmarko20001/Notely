"""scripts/01_transcribe.py's Groq backend: only the pure, dependency-free
logic is testable here without a live Groq account (network calls, actual
API response shapes, and real audio-size behavior are NOT covered -- see
the UNVERIFIED note on transcribe_with_groq itself and DOCUMENTATION.md
§5.1/TODO.md for why)."""

from conftest import load_stage

m1 = load_stage("01_transcribe.py")


def test_groq_field_reads_dict_style_response():
    assert m1._groq_field({"start": 1.0, "text": "hi"}, "start") == 1.0
    assert m1._groq_field({"start": 1.0, "text": "hi"}, "text") == "hi"


def test_groq_field_reads_object_style_response():
    class FakeSegment:
        start = 2.5
        text = "hello"

    assert m1._groq_field(FakeSegment(), "start") == 2.5
    assert m1._groq_field(FakeSegment(), "text") == "hello"


def test_groq_field_dict_missing_key_raises_keyerror():
    import pytest

    with pytest.raises(KeyError):
        m1._groq_field({"start": 1.0}, "text")


def test_groq_field_object_missing_attr_raises_attributeerror():
    import pytest

    class Empty:
        pass

    with pytest.raises(AttributeError):
        m1._groq_field(Empty(), "start")


def test_groq_max_upload_constant_matches_documented_free_tier_cap():
    # a change here should be deliberate (checked against Groq's current
    # docs), not an accidental edit -- this pins the value so a diff shows up
    assert m1.GROQ_MAX_UPLOAD_MB == 25
