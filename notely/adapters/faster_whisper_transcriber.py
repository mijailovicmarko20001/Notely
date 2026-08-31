"""Transcriber adapter backed by faster-whisper/ctranslate2 (CPU)."""

import os
from pathlib import Path

from ..ports import TranscriberError


class FasterWhisperTranscriber:
    """Real Transcriber, via faster-whisper. Default local backend."""

    def transcribe(
        self,
        lecture_id: str,
        wav_path: Path,
        model_size: str,
        forced_language: str | None,
        vocab_prompt: str,
    ) -> dict:
        try:
            from faster_whisper import WhisperModel  # lazy import, deps may not be installed
        except ImportError as exc:
            raise TranscriberError(f"faster-whisper not installed: {exc}") from exc

        # Benchmarked on Apple Silicon (90s Serbian audio, medium model):
        # float32/4-threads 2.0x realtime, float32/10t 1.9x, int8/10t 1.8x —
        # ctranslate2's defaults are already optimal there (float32 rides
        # the AMX units via Accelerate; int8 can't, and extra threads just
        # spin). Env overrides kept for non-Apple hardware, where int8
        # usually wins.
        cpu_threads = int(os.environ.get("WHISPER_CPU_THREADS", "0")) or 4
        compute_type = os.environ.get("WHISPER_COMPUTE", "auto")
        print(
            f"[whisper] {lecture_id}: loading faster-whisper model '{model_size}' "
            f"(cpu_threads={cpu_threads}, compute_type={compute_type})"
        )
        try:
            model = WhisperModel(model_size, cpu_threads=cpu_threads, compute_type=compute_type)

            # WHISPER_LANGUAGE pins the language (e.g. "sr"); unset ->
            # auto-detect. Auto-detection samples only the first 30s and
            # can land on a wrong close cousin (observed: Serbian lectures
            # detected as "bs" at low confidence, which measurably degrades
            # technical vocabulary).
            if forced_language:
                print(f"[whisper] {lecture_id}: transcribing (language pinned to '{forced_language}')...")
            else:
                print(f"[whisper] {lecture_id}: transcribing (auto-detecting language)...")
            # vad_filter skips silence — lecture pauses are where whisper
            # hallucinates repeated phrases; timestamps stay mapped to the
            # original timeline. Segment-level timestamps are sufficient,
            # so word_timestamps stays False.
            segments_iter, info = model.transcribe(
                str(wav_path),
                language=forced_language,
                initial_prompt=vocab_prompt or None,
                vad_filter=True,
            )
        except Exception as exc:  # noqa: BLE001 - any whisper/ctranslate2 failure
            raise TranscriberError(f"faster-whisper transcription failed: {exc}") from exc

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
