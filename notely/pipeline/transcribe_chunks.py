"""Shared audio-chunking helper for cloud transcription backends whose
upload API enforces a single-request size cap (Groq and OpenAI both cap
at 25MB, see notely/adapters/groq_transcriber.py and
openai_transcriber.py). A lecture's compressed audio easily exceeds that
past ~2 hours (see GROQ_MAX_UPLOAD_MB's original comment in
scripts/01_transcribe.py's history) -- this splits it into smaller pieces
via ffmpeg's segment muxer and stitches the per-chunk transcripts back
into one correctly-offset timeline.

Chunk-timestamp stitching is the highest-risk piece of this feature:
stage 5 aligns the transcript to slide windows using segment start/end
times, so a boundary bug here would silently corrupt every downstream
note for a chunked lecture. Offsets are computed from each chunk's real,
probed duration (see compute_chunk_offsets) rather than trusted to equal
the nominal segment_seconds -- ffmpeg's segment muxer splits on packet
boundaries near the requested time, not exactly on it, so trusting the
nominal spacing would drift timestamps by chunk 3+ on a long lecture.
"""

import subprocess
import time as _time
from pathlib import Path

from ..ports import AudioExtractorError

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 2.0


def call_with_retry(
    fn,
    is_retryable,
    max_retries=DEFAULT_MAX_RETRIES,
    backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
    sleep=None,
):
    """Call fn() (no args -- callers close over whatever they need),
    retrying up to max_retries attempts total with linear backoff
    (backoff_seconds * attempt_number) when is_retryable(exc) is True for
    the exception raised. An exception is_retryable rejects (e.g. a 400
    Bad Request, which will never succeed on retry) propagates
    immediately without retrying. Once retries are exhausted, re-raises
    the last exception seen -- callers wrap that in their own
    provider-specific error type (see groq_transcriber.py/
    openai_transcriber.py), never leaving a raw SDK exception or retry
    loop artifact to propagate on its own.

    sleep: injectable in place of time.sleep, for deterministic tests."""
    sleep = sleep or _time.sleep
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not is_retryable(exc) or attempt == max_retries:
                raise
            sleep(backoff_seconds * attempt)
    raise last_exc  # pragma: no cover - loop above always returns or raises


# Stay well under a provider's cap rather than hugging it exactly: Opus's
# variable bitrate and each chunk's own container overhead mean a chunk
# sized to land exactly at the cap can occasionally tip over it.
DEFAULT_CHUNK_SAFETY_MARGIN = 0.85
# Never chunk finer than this, regardless of how the safety-margin math
# comes out -- avoids pathological many-tiny-chunks behavior (extra
# request overhead, more chunk-boundary risk) if choose_chunk_seconds is
# ever called with a very small cap or corrupt bitrate estimate.
MIN_CHUNK_SECONDS = 60.0


def sdk_field(obj, key):
    """Cloud SDK responses may hand back objects (attribute access) or
    plain dicts (subscript access) depending on SDK/response-model
    version -- support both rather than guessing which. Shared by the
    Groq and OpenAI adapters (each SDK has shown both shapes across
    versions/endpoints)."""
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


def choose_chunk_seconds(
    audio_path: Path,
    total_duration: float,
    max_upload_bytes: int,
    safety_margin: float = DEFAULT_CHUNK_SAFETY_MARGIN,
) -> float:
    """Pick a chunk length, in seconds, computed from audio_path's own
    real bytes-per-second (not a hardcoded bitrate assumption -- the
    compression bitrate is configurable, see AudioExtractor.
    extract_compressed's bitrate param), so a single chunk comfortably
    stays under max_upload_bytes with safety_margin of headroom."""
    size = audio_path.stat().st_size
    if size <= 0 or total_duration <= 0:
        return max(total_duration, MIN_CHUNK_SECONDS)
    bytes_per_second = size / total_duration
    seconds = (max_upload_bytes * safety_margin) / bytes_per_second
    return max(MIN_CHUNK_SECONDS, seconds)


def split_audio_into_chunks(audio_path: Path, out_dir: Path, segment_seconds: float) -> list[Path]:
    """Split audio_path into segment_seconds-long pieces via ffmpeg's
    segment muxer, stream-copying (no re-encode -- the source is already
    compressed) and resetting each chunk's internal timestamps to 0 (so
    per-chunk duration probing and transcription both see a clean 0-based
    timeline). Returns chunk paths in order. Raises AudioExtractorError on
    failure."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem, suffix = audio_path.stem, audio_path.suffix
    pattern = out_dir / f"{stem}_chunk_%03d{suffix}"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-f",
        "segment",
        "-segment_time",
        str(segment_seconds),
        "-c",
        "copy",
        "-reset_timestamps",
        "1",
        str(pattern),
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise AudioExtractorError(f"ffmpeg failed splitting {audio_path}: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise AudioExtractorError(f"ffmpeg failed splitting {audio_path}:\n{stderr}")
    return sorted(out_dir.glob(f"{stem}_chunk_*{suffix}"))


def compute_chunk_offsets(chunk_paths: list, media_probe) -> list[float]:
    """Cumulative start-time offset for each chunk, from each preceding
    chunk's real probed duration -- see module docstring for why this
    can't just be `i * segment_seconds`. Raises AudioExtractorError if any
    chunk's duration can't be determined (a silently-wrong offset would
    corrupt every later chunk's timestamps, and stage 5 has no way to
    detect that after the fact -- fail loudly instead)."""
    offsets = []
    running = 0.0
    for path in chunk_paths:
        offsets.append(running)
        duration = media_probe.get_duration(path)
        if duration is None:
            raise AudioExtractorError(f"could not determine duration of chunk {path}")
        running += duration
    return offsets


def merge_chunk_transcripts(chunk_transcripts: list) -> dict:
    """chunk_transcripts: [(chunk_start_offset_seconds, {"language",
    "segments"}), ...] in chunk order (each per-chunk transcript's own
    segment times are 0-based, matching split_audio_into_chunks'
    -reset_timestamps). Offsets every segment by its chunk's start offset
    and concatenates. Language comes from the first chunk that reported
    one (an empty/unknown-language chunk, e.g. a silent lead-in, doesn't
    override a later chunk's real detection)."""
    language = ""
    segments = []
    for offset, transcript in chunk_transcripts:
        if not language:
            language = transcript.get("language") or ""
        for seg in transcript["segments"]:
            segments.append({"start": seg["start"] + offset, "end": seg["end"] + offset, "text": seg["text"]})
    return {"language": language, "segments": segments}


# Trailing text handed to the next chunk's prompt for continuity across a
# chunk boundary (a sentence split mid-word at the cut) -- same
# vocabulary-priming mechanism (Whisper's initial_prompt) build_vocabulary_
# prompt already uses, just fed the previous chunk's own tail instead of
# slide titles.
PRIOR_TAIL_CHARS = 200


def transcribe_audio_in_chunks(
    audio_path: Path, max_upload_bytes: int, transcribe_chunk_fn, media_probe=None, tmp_dir=None
) -> dict:
    """Transcribe audio_path, splitting into chunks only if it exceeds
    max_upload_bytes -- most lectures never do, and the common case is
    exactly one call to transcribe_chunk_fn over the whole file.

    transcribe_chunk_fn: (chunk_path, index_1_based, total, prior_tail:
    str) -> {"language", "segments"} (0-based segment times within that
    chunk). Called once per chunk in order; prior_tail is the previous
    chunk's own last segment text (capped), "" for the first chunk.

    media_probe: a MediaProbe (see notely.ports), defaults to the real
    ffprobe-backed adapter; tests inject a fake. tmp_dir: where chunk
    files are written, defaults to audio_path's own directory.
    """
    size = audio_path.stat().st_size
    if size <= max_upload_bytes:
        return transcribe_chunk_fn(audio_path, 1, 1, "")

    if media_probe is None:
        from ..adapters.ffprobe_media_probe import FfprobeMediaProbe

        media_probe = FfprobeMediaProbe()

    total_duration = media_probe.get_duration(audio_path)
    if total_duration is None:
        raise AudioExtractorError(f"could not determine duration of {audio_path} to plan chunking")

    segment_seconds = choose_chunk_seconds(audio_path, total_duration, max_upload_bytes)
    chunk_paths = split_audio_into_chunks(audio_path, tmp_dir or audio_path.parent, segment_seconds)
    offsets = compute_chunk_offsets(chunk_paths, media_probe)

    total = len(chunk_paths)
    results = []
    prior_tail = ""
    for index, (path, offset) in enumerate(zip(chunk_paths, offsets, strict=True), start=1):
        transcript = transcribe_chunk_fn(path, index, total, prior_tail)
        results.append((offset, transcript))
        segs = transcript.get("segments") or []
        prior_tail = segs[-1]["text"][-PRIOR_TAIL_CHARS:] if segs else ""

    return merge_chunk_transcripts(results)
