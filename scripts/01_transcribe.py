#!/usr/bin/env python3
"""
Stage [1]: Transcription
=========================

Transcribes a lecture video's audio track into a timestamped JSON transcript
using faster-whisper by default (local, no API key needed) -- or mlx (Apple
Silicon GPU) or a cloud backend (Groq/OpenAI) via WHISPER_BACKEND, see
Config below.

Steps per lecture (local backends -- cloud backends do their own,
compressed, extraction; see notely/adapters/groq_transcriber.py and
openai_transcriber.py):
  1. Extract mono 16kHz PCM WAV audio from the video with ffmpeg
     (`-vn -acodec pcm_s16le -ar 16000 -ac 1`), into a temp file.
  2. Transcribe the audio, auto-detecting the spoken language (never
     hardcoded, unless WHISPER_LANGUAGE pins it).
  3. Write output/transcripts/<lecture_id>.json:
       {"language": str, "segments": [{"start": float, "end": float, "text": str}, ...]}
  4. Clean up the temp audio file.

Usage:
    python scripts/01_transcribe.py <lecture_id>
    python scripts/01_transcribe.py --all
    python scripts/01_transcribe.py <lecture_id> --force

Config:
    WHISPER_MODEL     faster-whisper/mlx model size (default: "medium" --
                      "small" mis-detects Serbian as Bosnian, see
                      webui/config.py's DEFAULT_ENV). Ignored by cloud
                      backends, which have their own model env vars below.
    WHISPER_BACKEND   "faster-whisper" (default) | "mlx" | "groq" | "openai".
                      An unrecognized value is a hard error, not a silent
                      fallback. Cloud backends send lecture audio to a third
                      party -- opt-in only, never the default; see
                      DOCUMENTATION.md §5.1.
    WHISPER_LANGUAGE  Force a language (e.g. "sr") instead of auto-detect.
    GROQ_API_KEY / GROQ_WHISPER_MODEL       only read when WHISPER_BACKEND=groq.
    OPENAI_API_KEY / OPENAI_TRANSCRIBE_MODEL only read when WHISPER_BACKEND=openai
                      (model must support segment timestamps -- see
                      openai_transcriber.py's DEFAULT_OPENAI_MODEL comment).
    Loaded from .env if python-dotenv is available.

Notes:
    - Skips a lecture if output/transcripts/<lecture_id>.json already exists,
      unless --force is passed.
    - faster_whisper/groq/openai SDKs are imported lazily (inside the
      relevant adapter, only on the path that needs them) so that
      `python -m py_compile` and `--help` work even without every optional
      dependency installed.
    - A transcription failure (TranscriberError/AudioExtractorError, from
      any backend) is caught, printed to stderr, and returns False rather
      than propagating a raw traceback -- main() collects these across
      --all and exits nonzero if any lecture failed.
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
from notely.adapters.groq_transcriber import GroqTranscriber  # noqa: E402
from notely.adapters.mlx_transcriber import MlxTranscriber  # noqa: E402
from notely.adapters.openai_transcriber import OpenAiTranscriber  # noqa: E402
from notely.cli import require_lecture_id_or_all  # noqa: E402
from notely.env import DEFAULT_WHISPER_BACKEND, DEFAULT_WHISPER_MODEL, env_str  # noqa: E402
from notely.io import load_json, save_json  # noqa: E402
from notely.paths import PROJECT_ROOT  # noqa: E402
from notely.paths import SLIDES_DIR as INPUT_SLIDES_DIR  # noqa: E402
from notely.paths import VIDEOS_DIR as INPUT_VIDEOS_DIR  # noqa: E402
from notely.ports import AudioExtractorError, TranscriberError  # noqa: E402

OUTPUT_TRANSCRIPTS_DIR = PROJECT_ROOT / "output" / "transcripts"
SLIDES_EXTRACTED_DIR = PROJECT_ROOT / "output" / "slides_extracted"

# Local backends: Transcriber port, called with a pre-extracted wav_path.
# Cloud backends: CloudTranscriber port, called with the original
# video_path (they do their own, usually compressed, extraction) -- see
# notely/ports.py for why these are two different Protocols, not one.
# Registries (not a plain if/elif chain) so an unrecognized WHISPER_BACKEND
# value is a hard error below instead of silently falling back to
# faster-whisper, which is what happened before this was a registry.
LOCAL_TRANSCRIBER_BACKENDS = {
    "faster-whisper": FasterWhisperTranscriber,
    "mlx": MlxTranscriber,
}
CLOUD_TRANSCRIBER_BACKENDS = {
    "groq": GroqTranscriber,
    "openai": OpenAiTranscriber,
}

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

    # WHISPER_BACKEND=mlx uses Apple's MLX framework (M-series GPU): benchmarked
    # 20.1x realtime vs 5.2x for the CPU path on the same audio, same quality.
    # WHISPER_BACKEND=groq/openai send audio to a cloud API instead of
    # transcribing locally -- opt-in only, see notely/adapters/
    # groq_transcriber.py and openai_transcriber.py. Default remains
    # faster-whisper — mlx only exists on Apple Silicon, the cloud backends
    # need an API key and send lecture audio off-machine.
    backend = os.environ.get("WHISPER_BACKEND", DEFAULT_WHISPER_BACKEND).lower()

    tmp_wav_path = None
    try:
        forced_language = os.environ.get("WHISPER_LANGUAGE") or None
        vocab_prompt = build_vocabulary_prompt(lecture_id)
        if vocab_prompt:
            print(f"[whisper] {lecture_id}: priming with {len(vocab_prompt)} chars of slide vocabulary")

        # An explicitly injected transcriber (a test seam) bypasses backend
        # selection entirely, regardless of WHISPER_BACKEND's value -- same
        # as before this was a registry. Otherwise, an unrecognized backend
        # is a hard error: it used to silently fall back to faster-whisper,
        # which meant a typo in WHISPER_BACKEND (or a course's .env carrying
        # a stale value) ran the wrong backend with zero warning.
        if transcriber is not None:
            local_transcriber = transcriber
        elif backend in CLOUD_TRANSCRIBER_BACKENDS:
            local_transcriber = None
        elif backend in LOCAL_TRANSCRIBER_BACKENDS:
            local_transcriber = LOCAL_TRANSCRIBER_BACKENDS[backend]()
        else:
            known = ", ".join(sorted({*LOCAL_TRANSCRIBER_BACKENDS, *CLOUD_TRANSCRIBER_BACKENDS}))
            raise TranscriberError(f"unknown WHISPER_BACKEND={backend!r}; expected one of {known}")

        if local_transcriber is not None:
            tmp_fd, tmp_name = tempfile.mkstemp(suffix=".wav", prefix=f"{lecture_id}_")
            os.close(tmp_fd)
            tmp_wav_path = Path(tmp_name)
            audio_extractor.extract_wav(video_path, tmp_wav_path)
            transcript = local_transcriber.transcribe(
                lecture_id, tmp_wav_path, model_size, forced_language, vocab_prompt
            )
        else:
            # Cloud backends do their own (compressed) audio extraction
            # internally via the given AudioExtractor -- no local wav_path
            # to extract here at all, unlike the local branch above.
            cloud_transcriber = CLOUD_TRANSCRIBER_BACKENDS[backend]()
            transcript = cloud_transcriber.transcribe_video(
                lecture_id, video_path, forced_language, vocab_prompt, audio_extractor
            )

        # Temp file + atomic rename: a killed process (SIGKILL, docker stop,
        # host crash) can never leave a truncated-but-non-empty transcript
        # that the next run's exists()-and-nonempty skip check would trust.
        save_json(output_path, transcript)

        print(f"[done] {lecture_id}: wrote {len(transcript['segments'])} segments -> {output_path}")

    except (TranscriberError, AudioExtractorError) as e:
        print(f"[error] {lecture_id}: transcription failed: {e}", file=sys.stderr)
        return False
    finally:
        if tmp_wav_path is not None and tmp_wav_path.exists():
            tmp_wav_path.unlink()

    return True


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

    require_lecture_id_or_all(parser, args)

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
