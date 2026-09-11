"""notely.pipeline.transcribe_chunks -- shared audio-chunking helper for
cloud transcription backends whose upload API enforces a single-request
size cap (Groq and OpenAI both cap at 25MB). Written before the module
exists (test-first).

Chunk-timestamp stitching is the highest-risk piece of cloud
transcription: stage 5 aligns the transcript to slide windows using
segment start/end times, so a boundary bug here silently corrupts every
downstream note for lectures long enough to need chunking. This file
gets the most coverage as a result.
"""

import pytest
from fakes import FakeMediaProbe

from notely.pipeline import transcribe_chunks as tc
from notely.ports import AudioExtractorError

# --- sdk_field (dict-or-attribute response parsing, shared by both cloud
# adapters -- same tolerance groq/openai SDKs need since their typed
# response models don't always match what a synthetic/older/newer SDK
# version hands back) --------------------------------------------------


def test_sdk_field_reads_dict_style_response():
    assert tc.sdk_field({"start": 1.0, "text": "hi"}, "start") == 1.0


def test_sdk_field_reads_object_style_response():
    class Seg:
        start = 2.5

    assert tc.sdk_field(Seg(), "start") == 2.5


def test_sdk_field_dict_missing_key_raises_keyerror():
    with pytest.raises(KeyError):
        tc.sdk_field({"start": 1.0}, "text")


def test_sdk_field_object_missing_attr_raises_attributeerror():
    class Empty:
        pass

    with pytest.raises(AttributeError):
        tc.sdk_field(Empty(), "start")


# --- choose_chunk_seconds ------------------------------------------------


def test_choose_chunk_seconds_computed_from_real_bitrate(tmp_path):
    # 24kbps Opus -> ~180KB/min -> ~3KB/s; a 25MB cap should allow roughly
    # 25*1024*0.85/3 ≈ 7300s of headroom at that bitrate
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"x" * (3_000 * 600))  # 600s worth at ~3KB/s
    seconds = tc.choose_chunk_seconds(audio, total_duration=600.0, max_upload_bytes=25 * 1024 * 1024)
    assert seconds > 600  # the whole 600s file is comfortably under the cap at this bitrate


def test_choose_chunk_seconds_never_below_the_floor(tmp_path):
    # a pathologically high bitrate must not produce a near-zero chunk size
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"x" * (50 * 1024 * 1024))  # 50MB
    seconds = tc.choose_chunk_seconds(audio, total_duration=60.0, max_upload_bytes=1024)  # tiny cap
    assert seconds >= 60.0  # the documented floor


# --- split_audio_into_chunks (ffmpeg segment muxer, subprocess stubbed) ----


def _stub_ffmpeg_writing_chunks(monkeypatch, chunk_names):
    """Stub subprocess.run to behave like ffmpeg's segment muxer: writes
    placeholder files matching the given names into whatever directory
    the command's output pattern lives in."""

    def _fake_run(cmd, **kwargs):
        pattern = cmd[-1]
        out_dir = __import__("pathlib").Path(pattern).parent
        for name in chunk_names:
            (out_dir / name).write_bytes(b"chunk bytes")

        class _R:
            returncode = 0
            stderr = b""

        return _R()

    monkeypatch.setattr(tc.subprocess, "run", _fake_run)


def test_split_audio_into_chunks_returns_sorted_chunk_paths(tmp_path, monkeypatch):
    audio = tmp_path / "lecture01.ogg"
    audio.write_bytes(b"fake audio")
    out_dir = tmp_path / "chunks"
    _stub_ffmpeg_writing_chunks(
        monkeypatch, ["lecture01_chunk_001.ogg", "lecture01_chunk_000.ogg", "lecture01_chunk_002.ogg"]
    )

    chunks = tc.split_audio_into_chunks(audio, out_dir, segment_seconds=300.0)

    assert [p.name for p in chunks] == [
        "lecture01_chunk_000.ogg",
        "lecture01_chunk_001.ogg",
        "lecture01_chunk_002.ogg",
    ]


def test_split_audio_into_chunks_command_uses_segment_muxer_and_stream_copy(tmp_path, monkeypatch):
    audio = tmp_path / "lecture01.ogg"
    audio.write_bytes(b"fake audio")
    out_dir = tmp_path / "chunks"
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "lecture01_chunk_000.ogg").write_bytes(b"x")

        class _R:
            returncode = 0
            stderr = b""

        return _R()

    monkeypatch.setattr(tc.subprocess, "run", _fake_run)
    tc.split_audio_into_chunks(audio, out_dir, segment_seconds=300.0)

    cmd = captured["cmd"]
    assert "-f" in cmd and "segment" in cmd
    assert "-segment_time" in cmd and "300.0" in cmd
    assert "-c" in cmd and "copy" in cmd  # re-encode-free: source is already Opus


def test_split_audio_into_chunks_raises_on_nonzero_exit(tmp_path, monkeypatch):
    audio = tmp_path / "lecture01.ogg"
    audio.write_bytes(b"fake audio")

    def _fake_run(cmd, **kwargs):
        class _R:
            returncode = 1
            stderr = b"ffmpeg: invalid segment time"

        return _R()

    monkeypatch.setattr(tc.subprocess, "run", _fake_run)
    with pytest.raises(AudioExtractorError, match="invalid segment time"):
        tc.split_audio_into_chunks(audio, tmp_path / "chunks", segment_seconds=300.0)


def test_split_audio_into_chunks_raises_when_ffmpeg_missing(tmp_path, monkeypatch):
    def _raise(cmd, **kwargs):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(tc.subprocess, "run", _raise)
    with pytest.raises(AudioExtractorError):
        tc.split_audio_into_chunks(tmp_path / "a.ogg", tmp_path / "chunks", segment_seconds=300.0)


# --- compute_chunk_offsets (real per-chunk duration, not nominal spacing) ---


def test_compute_chunk_offsets_uses_real_durations_not_nominal_spacing():
    # ffmpeg's segment muxer splits on packet boundaries near the
    # requested time, not exactly on it -- offsets must accumulate each
    # chunk's REAL duration, or timestamps drift by chunk 3+ on a long
    # lecture.
    chunk_paths = ["c0.ogg", "c1.ogg", "c2.ogg"]
    probe = FakeMediaProbe(durations={"c0.ogg": 298.4, "c1.ogg": 301.9, "c2.ogg": 150.0})

    offsets = tc.compute_chunk_offsets(chunk_paths, probe)

    assert offsets == [0.0, 298.4, 298.4 + 301.9]


def test_compute_chunk_offsets_raises_if_a_chunk_duration_is_unknown():
    probe = FakeMediaProbe(durations={})  # every lookup returns None
    with pytest.raises(AudioExtractorError):
        tc.compute_chunk_offsets(["c0.ogg"], probe)


# --- merge_chunk_transcripts ------------------------------------------------


def test_merge_chunk_transcripts_offsets_every_segment_by_its_chunk_start():
    chunk_transcripts = [
        (0.0, {"language": "sr", "segments": [{"start": 0.0, "end": 10.0, "text": "a"}]}),
        (300.0, {"language": "sr", "segments": [{"start": 0.0, "end": 12.0, "text": "b"}]}),
    ]
    merged = tc.merge_chunk_transcripts(chunk_transcripts)
    assert merged["segments"] == [
        {"start": 0.0, "end": 10.0, "text": "a"},
        {"start": 300.0, "end": 312.0, "text": "b"},
    ]


def test_merge_chunk_transcripts_is_monotonically_increasing_across_three_chunks():
    chunk_transcripts = [
        (0.0, {"language": "sr", "segments": [{"start": 0.0, "end": 298.4, "text": "a"}]}),
        (298.4, {"language": "sr", "segments": [{"start": 0.0, "end": 301.9, "text": "b"}]}),
        (600.3, {"language": "sr", "segments": [{"start": 0.0, "end": 150.0, "text": "c"}]}),
    ]
    merged = tc.merge_chunk_transcripts(chunk_transcripts)
    starts_and_ends = [(s["start"], s["end"]) for s in merged["segments"]]
    flat = [t for pair in starts_and_ends for t in pair]
    assert flat == sorted(flat)  # strictly non-decreasing across the whole merged timeline
    # no overlap: each segment's start >= the previous segment's end
    for prev, cur in zip(merged["segments"], merged["segments"][1:], strict=False):
        assert cur["start"] >= prev["end"]


def test_merge_chunk_transcripts_language_comes_from_first_chunk_with_one():
    chunk_transcripts = [
        (0.0, {"language": "", "segments": []}),
        (10.0, {"language": "sr", "segments": []}),
        (20.0, {"language": "en", "segments": []}),
    ]
    merged = tc.merge_chunk_transcripts(chunk_transcripts)
    assert merged["language"] == "sr"


def test_merge_chunk_transcripts_single_chunk_passthrough():
    chunk_transcripts = [(0.0, {"language": "sr", "segments": [{"start": 1.0, "end": 2.0, "text": "x"}]})]
    merged = tc.merge_chunk_transcripts(chunk_transcripts)
    assert merged == {"language": "sr", "segments": [{"start": 1.0, "end": 2.0, "text": "x"}]}


# --- transcribe_audio_in_chunks (the orchestrator) --------------------------


def test_transcribe_audio_in_chunks_single_call_when_under_cap(tmp_path):
    audio = tmp_path / "lecture01.ogg"
    audio.write_bytes(b"x" * 100)  # tiny, well under any real cap
    calls = []

    def transcribe_one(chunk_path, index, total, prior_tail):
        calls.append((chunk_path, index, total, prior_tail))
        return {"language": "sr", "segments": [{"start": 0.0, "end": 5.0, "text": "hi"}]}

    result = tc.transcribe_audio_in_chunks(
        audio, max_upload_bytes=25 * 1024 * 1024, transcribe_chunk_fn=transcribe_one
    )

    assert calls == [(audio, 1, 1, "")]
    assert result == {"language": "sr", "segments": [{"start": 0.0, "end": 5.0, "text": "hi"}]}


def test_transcribe_audio_in_chunks_splits_and_stitches_when_over_cap(tmp_path, monkeypatch):
    audio = tmp_path / "lecture01.ogg"
    audio.write_bytes(b"x" * 200)  # "over cap" relative to the tiny cap below

    monkeypatch.setattr(
        tc,
        "split_audio_into_chunks",
        lambda path, out_dir, segment_seconds: [tmp_path / "c0.ogg", tmp_path / "c1.ogg"],
    )
    probe = FakeMediaProbe(
        durations={str(audio): 420.0, str(tmp_path / "c0.ogg"): 300.0, str(tmp_path / "c1.ogg"): 120.0}
    )

    calls = []

    def transcribe_one(chunk_path, index, total, prior_tail):
        calls.append((chunk_path.name, index, total, prior_tail))
        offset_marker = 0.0 if index == 1 else 0.0  # each chunk's own transcript starts at 0 locally
        return {
            "language": "sr",
            "segments": [{"start": offset_marker, "end": 10.0, "text": f"chunk{index}"}],
        }

    result = tc.transcribe_audio_in_chunks(
        audio, max_upload_bytes=100, transcribe_chunk_fn=transcribe_one, media_probe=probe
    )

    assert [c[:3] for c in calls] == [("c0.ogg", 1, 2), ("c1.ogg", 2, 2)]
    assert result["segments"] == [
        {"start": 0.0, "end": 10.0, "text": "chunk1"},
        {"start": 300.0, "end": 310.0, "text": "chunk2"},
    ]


def test_transcribe_audio_in_chunks_feeds_prior_chunks_tail_forward(tmp_path, monkeypatch):
    audio = tmp_path / "lecture01.ogg"
    audio.write_bytes(b"x" * 200)
    monkeypatch.setattr(
        tc,
        "split_audio_into_chunks",
        lambda path, out_dir, segment_seconds: [tmp_path / "c0.ogg", tmp_path / "c1.ogg"],
    )
    probe = FakeMediaProbe(
        durations={str(audio): 420.0, str(tmp_path / "c0.ogg"): 300.0, str(tmp_path / "c1.ogg"): 120.0}
    )

    tails_seen = []

    def transcribe_one(chunk_path, index, total, prior_tail):
        tails_seen.append(prior_tail)
        return {"language": "sr", "segments": [{"start": 0.0, "end": 1.0, "text": f"end of chunk {index}"}]}

    tc.transcribe_audio_in_chunks(
        audio, max_upload_bytes=100, transcribe_chunk_fn=transcribe_one, media_probe=probe
    )

    assert tails_seen[0] == ""  # no prior chunk yet
    assert "end of chunk 1" in tails_seen[1]  # continuity from the previous chunk's own transcript


def test_transcribe_audio_in_chunks_raises_if_duration_cannot_be_determined(tmp_path, monkeypatch):
    audio = tmp_path / "lecture01.ogg"
    audio.write_bytes(b"x" * 200)
    probe = FakeMediaProbe(durations={})  # get_duration(audio) -> None

    with pytest.raises(AudioExtractorError):
        tc.transcribe_audio_in_chunks(
            audio, max_upload_bytes=100, transcribe_chunk_fn=lambda *a: {}, media_probe=probe
        )


# --- call_with_retry (shared retry/backoff wrapper for the cloud SDK call
# each adapter makes per chunk) -----------------------------------------


def test_call_with_retry_returns_on_first_success():
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    result = tc.call_with_retry(fn, is_retryable=lambda e: True, sleep=lambda s: None)
    assert result == "ok"
    assert len(calls) == 1


def test_call_with_retry_retries_on_retryable_error_then_succeeds():
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("429 rate limited")
        return "ok"

    sleeps = []
    result = tc.call_with_retry(
        fn, is_retryable=lambda e: True, max_retries=5, sleep=lambda s: sleeps.append(s)
    )
    assert result == "ok"
    assert len(attempts) == 3
    assert len(sleeps) == 2  # slept between attempt 1->2 and 2->3, not after the final success


def test_call_with_retry_raises_original_exception_once_retries_exhausted():
    def fn():
        raise RuntimeError("still failing")

    with pytest.raises(RuntimeError, match="still failing"):
        tc.call_with_retry(fn, is_retryable=lambda e: True, max_retries=3, sleep=lambda s: None)


def test_call_with_retry_does_not_retry_a_non_retryable_error():
    calls = []

    def fn():
        calls.append(1)
        raise ValueError("400 bad request")

    with pytest.raises(ValueError, match="400 bad request"):
        tc.call_with_retry(fn, is_retryable=lambda e: False, max_retries=5, sleep=lambda s: None)
    assert len(calls) == 1  # never retried a non-retryable error


def test_call_with_retry_backoff_is_linear_in_attempt_number():
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("retryable")
        return "ok"

    sleeps = []
    tc.call_with_retry(
        fn, is_retryable=lambda e: True, max_retries=5, backoff_seconds=2.0, sleep=lambda s: sleeps.append(s)
    )
    assert sleeps == [2.0, 4.0]  # backoff_seconds * attempt_number
