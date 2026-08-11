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
    (default: "small"). Loaded from .env if python-dotenv is available.

Notes:
    - Skips a lecture if output/transcripts/<lecture_id>.json already exists,
      unless --force is passed.
    - faster_whisper is imported lazily (inside main/transcribe_lecture) so
      that `python -m py_compile` and `--help` work even without the
      dependency installed.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Project root = parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_VIDEOS_DIR = PROJECT_ROOT / "input" / "videos"
OUTPUT_TRANSCRIPTS_DIR = PROJECT_ROOT / "output" / "transcripts"
SLIDES_EXTRACTED_DIR = PROJECT_ROOT / "output" / "slides_extracted"
INPUT_SLIDES_DIR = PROJECT_ROOT / "input" / "slides"

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
            slides = json.loads(extracted.read_text())
            titles = [s.get("title", "") for s in slides]
        else:
            pdf = INPUT_SLIDES_DIR / f"{lecture_id}.pdf"
            if pdf.exists():
                import fitz  # lazy: only needed on this path

                with fitz.open(pdf) as doc:
                    for page in doc:
                        first_line = page.get_text().strip().split("\n", 1)[0]
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


def extract_audio(video_path: Path, wav_path: Path) -> None:
    """Extract mono 16kHz PCM WAV audio from a video with ffmpeg."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(wav_path),
    ]
    print(f"[audio] extracting audio: {video_path.name} -> {wav_path.name}")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise RuntimeError(f"ffmpeg failed extracting audio from {video_path}:\n{stderr}")


def transcribe_lecture(lecture_id: str, model_size: str, force: bool = False) -> None:
    """Transcribe a single lecture's video and write its transcript JSON."""
    video_path = INPUT_VIDEOS_DIR / f"{lecture_id}.mp4"
    output_path = OUTPUT_TRANSCRIPTS_DIR / f"{lecture_id}.json"

    if not video_path.exists():
        print(f"[skip] {lecture_id}: no video found at {video_path}", file=sys.stderr)
        return

    if output_path.exists() and not force:
        print(f"[skip] {lecture_id}: transcript already exists at {output_path} (use --force to redo)")
        return

    OUTPUT_TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".wav", prefix=f"{lecture_id}_")
    os.close(tmp_fd)
    tmp_wav_path = Path(tmp_name)

    # WHISPER_BACKEND=mlx uses Apple's MLX framework (M-series GPU): benchmarked
    # 20.1x realtime vs 5.2x for the CPU path on the same audio, same quality.
    # Default remains faster-whisper — mlx only exists on Apple Silicon.
    backend = os.environ.get("WHISPER_BACKEND", "faster-whisper").lower()

    try:
        extract_audio(video_path, tmp_wav_path)

        forced_language = os.environ.get("WHISPER_LANGUAGE") or None
        vocab_prompt = build_vocabulary_prompt(lecture_id)
        if vocab_prompt:
            print(f"[whisper] {lecture_id}: priming with {len(vocab_prompt)} chars of slide vocabulary")

        if backend == "mlx":
            transcript = transcribe_with_mlx(
                lecture_id, tmp_wav_path, model_size, forced_language, vocab_prompt
            )
        else:
            transcript = transcribe_with_faster_whisper(
                lecture_id, tmp_wav_path, model_size, forced_language, vocab_prompt
            )

        # Temp file + atomic rename: a killed process (SIGKILL, docker stop,
        # host crash) can never leave a truncated-but-non-empty transcript
        # that the next run's exists()-and-nonempty skip check would trust.
        tmp_output = output_path.with_name(f"{output_path.name}.tmp{os.getpid()}")
        with open(tmp_output, "w", encoding="utf-8") as f:
            json.dump(transcript, f, ensure_ascii=False, indent=2)
        tmp_output.replace(output_path)

        print(f"[done] {lecture_id}: wrote {len(transcript['segments'])} segments -> {output_path}")

    finally:
        if tmp_wav_path.exists():
            tmp_wav_path.unlink()


def transcribe_with_mlx(lecture_id, wav_path, model_size, forced_language, vocab_prompt) -> dict:
    """Apple-GPU transcription via mlx-whisper. Returns the transcript dict.

    verbose=True makes mlx print each segment as it's decoded
    ("[MM:SS.mmm --> MM:SS.mmm] text"), which doubles as live progress for
    the web UI's parser."""
    import mlx_whisper  # lazy import, Apple Silicon only

    repo = os.environ.get("WHISPER_MLX_REPO") or f"mlx-community/whisper-{model_size}"
    print(f"[whisper] {lecture_id}: transcribing on GPU via mlx ('{repo}', "
          f"language={'pinned ' + forced_language if forced_language else 'auto'})...")
    result = mlx_whisper.transcribe(
        str(wav_path),
        path_or_hf_repo=repo,
        language=forced_language,
        initial_prompt=vocab_prompt or None,
        verbose=True,
    )
    segments = [
        {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
        for s in result["segments"]
    ]
    return {"language": result.get("language") or forced_language or "", "segments": segments}


def transcribe_with_faster_whisper(lecture_id, wav_path, model_size, forced_language, vocab_prompt) -> dict:
    """CPU transcription via faster-whisper/ctranslate2. Returns the transcript dict."""
    from faster_whisper import WhisperModel  # lazy import, deps may not be installed

    # Benchmarked on Apple Silicon (90s Serbian audio, medium model):
    # float32/4-threads 2.0x realtime, float32/10t 1.9x, int8/10t 1.8x —
    # ctranslate2's defaults are already optimal there (float32 rides the
    # AMX units via Accelerate; int8 can't, and extra threads just spin).
    # Env overrides kept for non-Apple hardware, where int8 usually wins.
    cpu_threads = int(os.environ.get("WHISPER_CPU_THREADS", "0")) or 4
    compute_type = os.environ.get("WHISPER_COMPUTE", "auto")
    print(
        f"[whisper] {lecture_id}: loading faster-whisper model '{model_size}' "
        f"(cpu_threads={cpu_threads}, compute_type={compute_type})"
    )
    model = WhisperModel(model_size, cpu_threads=cpu_threads, compute_type=compute_type)

    # WHISPER_LANGUAGE pins the language (e.g. "sr"); unset -> auto-detect.
    # Auto-detection samples only the first 30s and can land on a wrong close
    # cousin (observed: Serbian lectures detected as "bs" at low confidence,
    # which measurably degrades technical vocabulary).
    if forced_language:
        print(f"[whisper] {lecture_id}: transcribing (language pinned to '{forced_language}')...")
    else:
        print(f"[whisper] {lecture_id}: transcribing (auto-detecting language)...")
    # vad_filter skips silence — lecture pauses are where whisper hallucinates
    # repeated phrases; timestamps stay mapped to the original timeline.
    # Segment-level timestamps are sufficient, so word_timestamps stays False.
    segments_iter, info = model.transcribe(
        str(wav_path),
        language=forced_language,
        initial_prompt=vocab_prompt or None,
        vad_filter=True,
    )

    detected_language = info.language
    print(
        f"[whisper] {lecture_id}: detected language = {detected_language} "
        f"(confidence={info.language_probability:.2f})"
    )

    segments = []
    for seg in segments_iter:
        text = seg.text.strip()
        segments.append({"start": seg.start, "end": seg.end, "text": text})
        # Whisper is slow on long lectures - log progress as segments stream in.
        print(f"  [{seg.start:8.2f} -> {seg.end:8.2f}] {text}")

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
    model_size = os.environ.get("WHISPER_MODEL", "small")

    if args.all:
        video_files = sorted(INPUT_VIDEOS_DIR.glob("*.mp4"))
        if not video_files:
            print(f"No videos found in {INPUT_VIDEOS_DIR}")
            return
        for video_path in video_files:
            transcribe_lecture(video_path.stem, model_size=model_size, force=args.force)
    else:
        transcribe_lecture(args.lecture_id, model_size=model_size, force=args.force)


if __name__ == "__main__":
    main()
