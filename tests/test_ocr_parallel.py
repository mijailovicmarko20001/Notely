"""notely.pipeline.matching's OCR pass: the dedup plan and its concurrent
execution.

OCR is stage 4's long pole by a wide margin (measured on this project's own
data: 445 frames at ~1.7s each, ~12 minutes, serial). The pass was
restructured into hash -> plan -> parallel-OCR so those independent calls can
overlap. These tests pin the part that must stay strictly sequential -- which
frame's text each event ends up with -- independently of the concurrency
around it, so a future change to the threading can't quietly alter dedup.
"""

from pathlib import Path

from notely.pipeline import matching
from notely.pipeline.example_detect import DHASH_DEDUP_THRESHOLD


class RecordingOcr:
    """Fake Ocr that returns a per-path canned text and records call order.

    Deliberately not tests.fakes.FakeOcr: these tests care about *how many*
    calls happened and for which frames, which is the whole point of dedup.
    """

    def __init__(self, texts=None):
        # Keyed by file *name*: ocr_events resolves frames against
        # PROJECT_ROOT, so the callee sees an absolute path, not the
        # project-relative one the event carries.
        self._texts = texts or {}
        self.calls = []

    def image_to_text(self, image_path, lang):
        name = Path(image_path).name
        self.calls.append(name)
        return self._texts.get(name, f"text-for-{name}")


def spread_hash(path):
    """A distinct hash per frame, deliberately far apart in Hamming distance.

    Naive `int(name)` gives 0,1,2,3... which differ by only 1-2 bits and are
    therefore all *within* DHASH_DEDUP_THRESHOLD -- every frame would read as
    a near-duplicate of its predecessor, which is the opposite of what these
    tests set up. Multiplying spreads consecutive values >= 8 bits apart."""
    return int(path.name[:3]) * 0x11111111


def _events(n):
    return [{"timestamp": i * 10.0, "frame_image_path": f"output/f/{i:03d}.png"} for i in range(n)]


# --- plan_ocr: the sequential dedup rule ------------------------------------


def test_plan_ocr_all_distinct_frames_each_own_their_text():
    # hashes far apart -> nothing is a near-duplicate
    hashes = {0: 0b0000, 1: 0b1111_1111, 2: 0b1111_1111_1111_0000}
    assert matching.plan_ocr(hashes, 3) == [0, 1, 2]


def test_plan_ocr_identical_consecutive_frames_share_the_first():
    hashes = {0: 42, 1: 42, 2: 42}
    assert matching.plan_ocr(hashes, 3) == [0, 0, 0]


def test_plan_ocr_first_frame_always_owns_its_text():
    """There is no predecessor to copy from, whatever the hash."""
    assert matching.plan_ocr({0: 7}, 1) == [0]


def test_plan_ocr_chain_of_near_duplicates_resolves_to_the_head_of_the_run():
    """Each frame is compared to its immediate predecessor, so a drifting
    chain collapses onto the frame that started it -- matching what the old
    sequential loop did by carrying prev_ocr_text forward."""
    # each successive hash differs from the previous by 1 bit (within threshold)
    hashes = {0: 0b0000, 1: 0b0001, 2: 0b0011, 3: 0b0111}
    assert matching.plan_ocr(hashes, 4) == [0, 0, 0, 0]


def test_plan_ocr_run_ends_when_a_frame_differs_enough():
    hashes = {0: 0, 1: 0, 2: 0xFFFF, 3: 0xFFFF}
    assert matching.plan_ocr(hashes, 4) == [0, 0, 2, 2]


def test_plan_ocr_boundary_is_inclusive_at_the_threshold():
    """Distance exactly == DHASH_DEDUP_THRESHOLD still counts as duplicate."""
    at_threshold = (1 << DHASH_DEDUP_THRESHOLD) - 1  # exactly N bits set
    assert bin(at_threshold).count("1") == DHASH_DEDUP_THRESHOLD
    assert matching.plan_ocr({0: 0, 1: at_threshold}, 2) == [0, 0]


def test_plan_ocr_one_bit_past_the_threshold_is_a_new_frame():
    past = (1 << (DHASH_DEDUP_THRESHOLD + 1)) - 1
    assert bin(past).count("1") == DHASH_DEDUP_THRESHOLD + 1
    assert matching.plan_ocr({0: 0, 1: past}, 2) == [0, 1]


def test_plan_ocr_missing_hash_is_treated_as_not_a_duplicate():
    """frame_hash can't fail silently today, but a missing entry must not
    make an event inherit unrelated text."""
    assert matching.plan_ocr({0: 5}, 2) == [0, 1]


# --- ocr_events: hashing + parallel execution -------------------------------


def test_ocr_events_calls_ocr_once_per_distinct_frame(monkeypatch):
    monkeypatch.setattr(matching, "frame_hash", lambda p: {"000": 1, "001": 1, "002": 999}[p.name[:3]])
    events = _events(3)
    ocr = RecordingOcr()

    skipped = matching.ocr_events(events, {}, ocr, "eng")

    assert len(ocr.calls) == 2, "the duplicate frame must not be OCR'd"
    assert skipped == 1


def test_ocr_events_duplicate_inherits_its_predecessors_text(monkeypatch):
    monkeypatch.setattr(matching, "frame_hash", lambda p: {"000": 1, "001": 1, "002": 999}[p.name[:3]])
    events = _events(3)

    matching.ocr_events(events, {}, RecordingOcr(), "eng")

    assert events[0]["ocr_text"] == events[1]["ocr_text"] != events[2]["ocr_text"]


def test_ocr_events_populates_a_hash_for_every_event(monkeypatch):
    """Example detection measures ink drift frame-to-frame, so it needs a
    hash for every event -- including ones whose OCR was skipped."""
    monkeypatch.setattr(matching, "frame_hash", spread_hash)
    events = _events(5)
    hashes = {}

    matching.ocr_events(events, hashes, RecordingOcr(), "eng")

    assert sorted(hashes) == [0, 1, 2, 3, 4]


def test_ocr_events_every_event_gets_text(monkeypatch):
    monkeypatch.setattr(matching, "frame_hash", lambda p: int(p.name[:3]) // 2)
    events = _events(6)

    matching.ocr_events(events, {}, RecordingOcr(), "eng")

    assert all(e["ocr_text"] for e in events)


def test_ocr_events_result_is_independent_of_completion_order(monkeypatch):
    """The pool may finish out of order; texts are collected by index, so the
    mapping frame -> text must not depend on that."""
    monkeypatch.setattr(matching, "frame_hash", spread_hash)
    events = _events(8)
    texts = {f"{i:03d}.png": f"T{i}" for i in range(8)}

    matching.ocr_events(events, {}, RecordingOcr(texts), "eng")

    assert [e["ocr_text"] for e in events] == [f"T{i}" for i in range(8)]


def test_ocr_events_passes_the_language_through(monkeypatch):
    seen = []

    class LangSpy(RecordingOcr):
        def image_to_text(self, image_path, lang):
            seen.append(lang)
            return "x"

    monkeypatch.setattr(matching, "frame_hash", spread_hash)
    matching.ocr_events(_events(3), {}, LangSpy(), "srp+srp_latn+eng")

    assert set(seen) == {"srp+srp_latn+eng"}


def test_ocr_events_handles_a_single_event(monkeypatch):
    monkeypatch.setattr(matching, "frame_hash", lambda p: 1)
    events = _events(1)

    skipped = matching.ocr_events(events, {}, RecordingOcr(), "eng")

    assert skipped == 0
    assert events[0]["ocr_text"]


def test_ocr_concurrency_respects_the_env_override(monkeypatch):
    monkeypatch.setenv("OCR_CONCURRENCY", "3")
    assert matching._ocr_concurrency() == 3


def test_ocr_concurrency_falls_back_on_a_bad_value(monkeypatch):
    monkeypatch.setenv("OCR_CONCURRENCY", "not-a-number")
    assert matching._ocr_concurrency() >= 1


def test_ocr_concurrency_is_never_zero_or_negative(monkeypatch):
    monkeypatch.setenv("OCR_CONCURRENCY", "0")
    assert matching._ocr_concurrency() == 1
