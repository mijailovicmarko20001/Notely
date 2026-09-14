"""CloudTranscriber adapter backed by OpenAI's transcription API.

Second cloud provider (see notely/ports.py::CloudTranscriber), added for
Serbian transcription quality: this course's audio is Serbian, and
OpenAI's Whisper-lineage models have proven Serbian coverage, where
Deepgram's is thin -- Deepgram becomes a drop-in behind this same port if
that judgment ever needs revisiting.

Model choice is deliberately pinned to whisper-1, not the newer
gpt-4o-transcribe/gpt-4o-mini-transcribe models: those don't support
response_format="verbose_json" or segment-level timestamps at all, and
timestamps are load-bearing here -- stage 5 aligns the transcript to
slide windows using segment start/end times. A model that can't provide
them would silently break the pipeline, not just produce lower-quality
notes, so this isn't a quality/cost tradeoff to leave to a default the
way it might be for a plain transcript-only use case.

Same shape as groq_transcriber.py (chunking, retry, response parsing) --
see that module's own comments for anything not repeated here.
"""

import os
import shutil
import tempfile
import uuid
from pathlib import Path

from ..adapters.ffprobe_media_probe import FfprobeMediaProbe
from ..pipeline import transcribe_chunks
from ..ports import CloudTranscriberError

OPENAI_MAX_UPLOAD_MB = 25  # OpenAI's transcription endpoint cap, as of when this was written
DEFAULT_OPENAI_MODEL = "whisper-1"  # must support response_format=verbose_json + segment timestamps

_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class OpenAiTranscriber:
    """Real CloudTranscriber, via OpenAI's transcription API."""

    def transcribe_video(
        self,
        lecture_id: str,
        video_path: Path,
        forced_language: str | None,
        vocab_prompt: str,
        audio_extractor,
    ) -> dict:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise CloudTranscriberError(
                "WHISPER_BACKEND=openai requires OPENAI_API_KEY (get one at https://platform.openai.com/api-keys)"
            )

        from openai import OpenAI  # lazy import; optional dependency, see requirements.txt

        client = OpenAI(api_key=api_key)
        model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", DEFAULT_OPENAI_MODEL)

        tmp_fd, tmp_name = tempfile.mkstemp(suffix=".ogg", prefix=f"{lecture_id}_openai_")
        os.close(tmp_fd)
        compressed_path = Path(tmp_name)
        chunk_dir = compressed_path.parent / f"{lecture_id}_openai_chunks_{uuid.uuid4().hex[:8]}"
        try:
            audio_extractor.extract_compressed(video_path, compressed_path)

            def transcribe_one_chunk(chunk_path, index, total, prior_tail):
                prompt = vocab_prompt
                if prior_tail:
                    prompt = f"{prompt} {prior_tail}".strip() if prompt else prior_tail
                what = f"[chunk {index}/{total}] " if total > 1 else ""
                print(
                    f"[whisper] {lecture_id}: {what}transcribing via OpenAI API (model='{model}', "
                    f"language={'pinned ' + forced_language if forced_language else 'auto'})..."
                )
                return self._transcribe_chunk(client, chunk_path, model, forced_language, prompt)

            transcript = transcribe_chunks.transcribe_audio_in_chunks(
                compressed_path,
                OPENAI_MAX_UPLOAD_MB * 1024 * 1024,
                transcribe_one_chunk,
                media_probe=FfprobeMediaProbe(),
                tmp_dir=chunk_dir,
            )
            print(
                f"[whisper] {lecture_id}: OpenAI transcription complete, "
                f"{len(transcript['segments'])} segment(s), language={transcript['language']}"
            )
            return transcript
        finally:
            compressed_path.unlink(missing_ok=True)
            if chunk_dir.exists():
                shutil.rmtree(chunk_dir, ignore_errors=True)

    def _transcribe_chunk(self, client, chunk_path: Path, model: str, forced_language, prompt: str) -> dict:
        def call():
            with open(chunk_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    file=(chunk_path.name, f.read()),
                    model=model,
                    language=forced_language,
                    prompt=prompt or None,
                    response_format="verbose_json",
                )
            return self._parse_response(response, forced_language)

        try:
            return transcribe_chunks.call_with_retry(call, is_retryable=self._is_retryable)
        except CloudTranscriberError:
            raise
        except Exception as exc:
            raise CloudTranscriberError(f"OpenAI transcription failed: {exc}") from exc

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        return getattr(exc, "status_code", None) in _RETRYABLE_STATUS_CODES

    @staticmethod
    def _parse_response(response, forced_language) -> dict:
        try:
            raw_segments = transcribe_chunks.sdk_field(response, "segments")
            segments = [
                {
                    "start": float(transcribe_chunks.sdk_field(s, "start")),
                    "end": float(transcribe_chunks.sdk_field(s, "end")),
                    "text": transcribe_chunks.sdk_field(s, "text").strip(),
                }
                for s in raw_segments
            ]
        except (KeyError, AttributeError, TypeError) as exc:
            raise CloudTranscriberError(f"OpenAI response missing expected segment data: {exc}") from exc

        try:
            detected_language = transcribe_chunks.sdk_field(response, "language")
        except (KeyError, AttributeError):
            detected_language = None
        detected_language = detected_language or forced_language or ""
        return {"language": detected_language, "segments": segments}
