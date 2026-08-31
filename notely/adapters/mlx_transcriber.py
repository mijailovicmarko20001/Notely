"""Transcriber adapter backed by mlx-whisper (Apple-GPU, M-series only)."""

import os
from pathlib import Path

from ..ports import TranscriberError


class MlxTranscriber:
    """Real Transcriber, via mlx-whisper. WHISPER_BACKEND=mlx -- benchmarked
    20.1x realtime vs 5.2x for the CPU (faster-whisper) path on the same
    audio, same quality; only exists on Apple Silicon."""

    def transcribe(
        self,
        lecture_id: str,
        wav_path: Path,
        model_size: str,
        forced_language: str | None,
        vocab_prompt: str,
    ) -> dict:
        try:
            import mlx_whisper  # lazy import, Apple Silicon only
        except ImportError as exc:
            raise TranscriberError(f"mlx-whisper not installed: {exc}") from exc

        repo = os.environ.get("WHISPER_MLX_REPO") or f"mlx-community/whisper-{model_size}"
        print(
            f"[whisper] {lecture_id}: transcribing on GPU via mlx ('{repo}', "
            f"language={'pinned ' + forced_language if forced_language else 'auto'})..."
        )
        try:
            # verbose=True makes mlx print each segment as it's decoded
            # ("[MM:SS.mmm --> MM:SS.mmm] text"), which doubles as live
            # progress for the web UI's parser.
            result = mlx_whisper.transcribe(
                str(wav_path),
                path_or_hf_repo=repo,
                language=forced_language,
                initial_prompt=vocab_prompt or None,
                verbose=True,
            )
        except Exception as exc:  # noqa: BLE001 - any mlx-whisper failure
            raise TranscriberError(f"mlx-whisper transcription failed: {exc}") from exc

        segments = [
            {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
            for s in result["segments"]
        ]
        return {"language": result.get("language") or forced_language or "", "segments": segments}
