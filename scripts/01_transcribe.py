#!/usr/bin/env python3
"""
Stage [1]: Transcription
=========================

Transcribes a lecture video's audio track into a timestamped JSON transcript
using faster-whisper (local, no API key needed).

Steps per lecture:
  1. Extract mono 16kHz PCM WAV audio from the video with ffmpeg
     (`-vn -acodec pcm_s16le -ar 16000 -ac 1`), into a temp file.
  2. Transcribe the audio with faster-whisper, auto-detecting the spoken
     language (never hardcoded).
  3. Write output/transcripts/<lecture_id>.json:
       {"language": str, "segments": [{"start": float, "end": float, "text": str}, ...]}
  4. Clean up the temp audio file.

Usage:
    python scripts/01_transcribe.py <lecture_id>
    python scripts/01_transcribe.py --all
    python scripts/01_transcribe.py <lecture_id> --force

Config:
    WHISPER_MODEL env var selects the faster-whisper model size
    (default: "medium" -- "small" mis-detects Serbian as Bosnian, see
    webui/config.py's DEFAULT_ENV). Loaded from .env if python-dotenv is
    available.

Notes:
    - Skips a lecture if output/transcripts/<lecture_id>.json already exists,
      unless --force is passed.
    - faster_whisper is imported lazily (inside main/transcribe_lecture) so
      that `python -m py_compile` and `--help` work even without the
      dependency installed.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

# Only needed to bootstrap the `from notely...` import below (finding
# notely/ on sys.path) -- notely.paths.PROJECT_ROOT is the same value and
# is what the rest of this file uses.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent

# notely/ (ports, adapters, paths) lives alongside scripts/ and webui/ at
# the project root, not on sys.path by default when this file is run
# directly (python scripts/01_transcribe.py) -- same fix
# tests/conftest.py applies for test discovery. Must happen before the
# `from notely...` import below.
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.adapters.faster_whisper_transcriber import FasterWhisperTranscriber  # noqa: E402
from notely.adapters.ffmpeg_audio_extractor import FfmpegAudioExtractor  # noqa: E402
from notely.adapters.mlx_transcriber import MlxTranscriber  # noqa: E402
from notely.env import DEFAULT_WHISPER_MODEL, env_str  # noqa: E402
from notely.io import load_json, save_json  # noqa: E402
from notely.paths import PROJECT_ROOT  # noqa: E402
from notely.paths import SLIDES_DIR as INPUT_SLIDES_DIR  # noqa: E402
from notely.paths import VIDEOS_DIR as INPUT_VIDEOS_DIR  # noqa: E402

OUTPUT_TRANSCRIPTS_DIR = PROJECT_ROOT / "output" / "transcripts"
SLIDES_EXTRACTED_DIR = PROJECT_ROOT / "output" / "slides_extracted"

# Whisper's initial_prompt is capped at ~224 tokens; stay safely under it.
VOCAB_PROMPT_MAX_CHARS = 700


def build_vocabulary_prompt(lecture_id: str) -> str:
    """Course terminology from the lecture's slides, used as whisper's
    initial_prompt so domain terms are transcribed correctly. Prefers the
    stage-2 extraction if it exists; otherwise reads titles straight from the
    PDF (stage 2 normally runs after transcription)."""
    titles = []
    extracted = SLIDES_EXTRACTED_DIR / f"{lecture_id}.json"
    try:
        if extracted.exists():
            slides = load_json(extracted)
            titles = [s.get("title", "") for s in slides]
        else:
            pdf = INPUT_SLIDES_DIR / f"{lecture_id}.pdf"
            if pdf.exists():
                import pypdfium2 as pdfium  # lazy: only needed on this path

                with pdfium.PdfDocument(str(pdf)) as doc:
                    for page in doc:
                        textpage = page.get_textpage()
                        try:
                            first_line = textpage.get_text_range().strip().split("\n", 1)[0]
                        finally:
                            textpage.close()
                        page.close()
                        titles.append(first_line)
    except Exception:
        return ""  # vocabulary priming is best-effort, never fatal

    seen, terms = set(), []
    for t in titles:
        t = t.strip()
        if t and t not in seen and len(t) < 80:
            seen.add(t)
            terms.append(t)
    prompt = ", ".join(terms)
    return prompt[:VOCAB_PROMPT_MAX_CHARS]


def transcribe_lecture(
    lecture_id: str, model_size: str, force: bool = False, audio_extractor=None, transcriber=None
) -> bool:
    """Transcribe a single lecture's video and write its transcript JSON.
    Returns False only when a required input was missing (the caller should
    treat that as a failure); an already-done skip and a real successful
    run both return True.

    audio_extractor: an AudioExtractor (see notely.ports), defaults to the
    real ffmpeg-backed adapter. transcriber: a Transcriber, defaults to a
    backend selected by WHISPER_BACKEND (see below) -- pass one explicitly
    to bypass that selection entirely (e.g. in a test). Both default to
    their real adapter; tests inject a fake instead of needing
    ffmpeg/faster-whisper/mlx installed."""
    if audio_extractor is None:
        audio_extractor = FfmpegAudioExtractor()
    video_path = INPUT_VIDEOS_DIR / f"{lecture_id}.mp4"
    output_path = OUTPUT_TRANSCRIPTS_DIR / f"{lecture_id}.json"

    if not video_path.exists():
        print(f"[skip] {lecture_id}: no video found at {video_path}", file=sys.stderr)
        return False

    if output_path.exists() and not force:
        print(f"[skip] {lecture_id}: transcript already exists at {output_path} (use --force to redo)")
        return True

    OUTPUT_TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".wav", prefix=f"{lecture_id}_")
    os.close(tmp_fd)
    tmp_wav_path = Path(tmp_name)

    # WHISPER_BACKEND=mlx uses Apple's MLX framework (M-series GPU): benchmarked
    # 20.1x realtime vs 5.2x for the CPU path on the same audio, same quality.
    # WHISPER_BACKEND=groq sends audio to Groq's hosted Whisper API instead of
    # transcribing locally -- opt-in only, see transcribe_with_groq. Default
    # remains faster-whisper — mlx only exists on Apple Silicon, groq needs an
    # API key and sends lecture audio off-machine.
    backend = os.environ.get("WHISPER_BACKEND", "faster-whisper").lower()

    try:
        forced_language = os.environ.get("WHISPER_LANGUAGE") or None
        vocab_prompt = build_vocabulary_prompt(lecture_id)
        if vocab_prompt:
            print(f"[whisper] {lecture_id}: priming with {len(vocab_prompt)} chars of slide vocabulary")

        if backend == "groq":
            # Groq does its own (compressed) audio extraction, since it needs
            # a small upload rather than the uncompressed WAV the local
            # backends use -- no need to also extract_wav() here.
            transcript = transcribe_with_groq(
                lecture_id, video_path, forced_language, vocab_prompt, audio_extractor
            )
        else:
            audio_extractor.extract_wav(video_path, tmp_wav_path)
            active_transcriber = transcriber
            if active_transcriber is None:
                active_transcriber = MlxTranscriber() if backend == "mlx" else FasterWhisperTranscriber()
            transcript = active_transcriber.transcribe(
                lecture_id, tmp_wav_path, model_size, forced_language, vocab_prompt
            )

        # Temp file + atomic rename: a killed process (SIGKILL, docker stop,
        # host crash) can never leave a truncated-but-non-empty transcript
        # that the next run's exists()-and-nonempty skip check would trust.
        save_json(output_path, transcript)

        print(f"[done] {lecture_id}: wrote {len(transcript['segments'])} segments -> {output_path}")

    finally:
        if tmp_wav_path.exists():
            tmp_wav_path.unlink()

    return True


GROQ_MAX_UPLOAD_MB = 25  # Groq's free-tier audio upload cap, as of when this was written


def _groq_field(obj, key):
    """Groq SDK responses may hand back objects (attribute access) or
    plain dicts (subscript access) depending on SDK version -- support
    both rather than guessing which. Works for the top-level response
    (e.g. "language") as well as each segment (e.g. "start"/"end"/"text")."""
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


def transcribe_with_groq(lecture_id, video_path, forced_language, vocab_prompt, audio_extractor) -> dict:
    """Cloud transcription via Groq's hosted Whisper API.

    NOT run against a live Groq account (no API key was available while
    building this) -- that gap is real, but narrower than it sounds: the
    installed `groq` SDK's own request signature and response parsing were
    checked directly (not just assumed from docs). `client.audio.
    transcriptions.create()`'s real parameters match what's passed below
    exactly. More importantly, verbose_json's extra fields (`language`,
    `segments`, `duration`) aren't in the SDK's strictly-typed response
    model (which only declares `text`) -- but the model uses Pydantic
    `extra="allow"`, and feeding it a synthetic verbose_json payload
    confirmed `.language` comes back via plain attribute access while
    `.segments` comes back as a list of *plain dicts*, not nested
    objects -- exactly what _groq_field's dict-or-attribute fallback below
    is built to handle (pure attribute access on segments would have
    crashed). What's genuinely unverified is the network round-trip itself
    (auth, rate limits, the model name being currently valid, real audio
    producing the same response shape as a synthetic test payload) --
    run it once against a short lecture before trusting it for real, and
    check https://console.groq.com/docs/speech-to-text for API changes
    since this was written.

    Privacy note: unlike every other backend, this sends lecture audio to
    a third party (Groq). Opt-in only via WHISPER_BACKEND=groq — never the
    default — exactly because of that trade-off; see DOCUMENTATION.md §5.1
    for the fuller discussion of why cloud transcription stays opt-in in
    this project.

    Mainly useful on machines without a usable local GPU (this project's
    own machine already has a faster local path via WHISPER_BACKEND=mlx,
    so this backend doesn't help there -- it exists for other students'
    hardware).
    """
    from groq import Groq  # lazy import; optional dependency, see requirements.txt

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "WHISPER_BACKEND=groq requires GROQ_API_KEY (get one at https://console.groq.com/keys)"
        )

    # Groq's API has a request size cap (25MB on the free tier, per its
    # docs as of when this was written). Uncompressed 16kHz mono PCM WAV
    # blows past that for anything over ~15 minutes, so encode audio
    # directly from the video into a small compressed file instead of
    # reusing the WAV the other backends extract.
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".ogg", prefix=f"{lecture_id}_groq_")
    os.close(tmp_fd)
    compressed_path = Path(tmp_name)
    try:
        audio_extractor.extract_compressed(video_path, compressed_path)
        size_mb = compressed_path.stat().st_size / (1024 * 1024)
        if size_mb > GROQ_MAX_UPLOAD_MB:
            raise RuntimeError(
                f"compressed audio is {size_mb:.1f}MB, over Groq's ~{GROQ_MAX_UPLOAD_MB}MB "
                "single-upload limit -- this lecture is too long for a single-file Groq "
                "request. Chunking isn't implemented (real scope: re-stitching timestamps "
                "across chunks correctly needs testing against a live account this "
                "backend never had). Use WHISPER_BACKEND=faster-whisper or mlx for this "
                "lecture instead."
            )

        model = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
        print(
            f"[whisper] {lecture_id}: transcribing via Groq API (model='{model}', "
            f"upload={size_mb:.1f}MB, language={'pinned ' + forced_language if forced_language else 'auto'})..."
        )

        client = Groq(api_key=api_key)
        with open(compressed_path, "rb") as f:
            response = client.audio.transcriptions.create(
                file=(compressed_path.name, f.read()),
                model=model,
                language=forced_language,
                prompt=vocab_prompt or None,
                response_format="verbose_json",
            )
    finally:
        if compressed_path.exists():
            compressed_path.unlink()

    raw_segments = _groq_field(response, "segments")
    segments = [
        {
            "start": float(_groq_field(s, "start")),
            "end": float(_groq_field(s, "end")),
            "text": _groq_field(s, "text").strip(),
        }
        for s in raw_segments
    ]
    try:
        detected_language = _groq_field(response, "language")
    except (KeyError, AttributeError):
        detected_language = None
    detected_language = detected_language or forced_language or ""
    print(
        f"[whisper] {lecture_id}: Groq transcription complete, {len(segments)} segment(s), language={detected_language}"
    )
    return {"language": detected_language, "segments": segments}


def load_dotenv_if_available() -> None:
    """Best-effort .env loading; never fatal if python-dotenv isn't installed."""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="Stage [1]: Transcribe lecture video audio with faster-whisper."
    )
    parser.add_argument(
        "lecture_id",
        nargs="?",
        default=None,
        help="Lecture id, e.g. lecture01 (matches input/videos/<lecture_id>.mp4)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Transcribe every video found in input/videos/",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-transcribe even if output/transcripts/<lecture_id>.json already exists",
    )
    args = parser.parse_args()

    if bool(args.all) == bool(args.lecture_id):
        parser.error("provide exactly one of <lecture_id> or --all")

    load_dotenv_if_available()
    model_size = env_str("WHISPER_MODEL", DEFAULT_WHISPER_MODEL)

    failures = []
    if args.all:
        video_files = sorted(INPUT_VIDEOS_DIR.glob("*.mp4"))
        if not video_files:
            print(f"No videos found in {INPUT_VIDEOS_DIR}")
            return
        for video_path in video_files:
            if not transcribe_lecture(video_path.stem, model_size=model_size, force=args.force):
                failures.append(video_path.stem)
    else:
        if not transcribe_lecture(args.lecture_id, model_size=model_size, force=args.force):
            failures.append(args.lecture_id)

    if failures:
        print(f"FAILED: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
