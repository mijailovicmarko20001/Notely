"""CloudTranscriber adapter backed by Groq's hosted Whisper API.

Moved here from scripts/01_transcribe.py::transcribe_with_groq (which was
never run against a live Groq account) as part of hardening it: implements
the CloudTranscriber port properly instead of being a special-cased
function scripts/01 branched to directly, adds retry/backoff on
transient failures, and transparently chunks audio that exceeds Groq's
upload cap instead of hard-failing the whole lecture (see
notely.pipeline.transcribe_chunks).

Privacy note: unlike every local backend, this sends lecture audio to a
third party (Groq). Opt-in only via WHISPER_BACKEND=groq -- never the
default -- exactly because of that trade-off; see DOCUMENTATION.md §5.1.
"""

import os
import shutil
import tempfile
import uuid
from pathlib import Path

from ..adapters.ffprobe_media_probe import FfprobeMediaProbe
from ..pipeline import transcribe_chunks
from ..ports import CloudTranscriberError

GROQ_MAX_UPLOAD_MB = 25  # Groq's free-tier single-upload cap, as of when this was written
DEFAULT_GROQ_MODEL = "whisper-large-v3-turbo"

# HTTP statuses worth retrying: rate limits and transient server-side
# failures. Anything else (400 malformed request, 401/403 auth) will
# never succeed on retry -- retrying those just wastes the retry budget
# and delays the real error reaching the user.
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class GroqTranscriber:
    """Real CloudTranscriber, via Groq's hosted Whisper API."""

    def transcribe_video(
        self,
        lecture_id: str,
        video_path: Path,
        forced_language: str | None,
        vocab_prompt: str,
        audio_extractor,
    ) -> dict:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise CloudTranscriberError(
                "WHISPER_BACKEND=groq requires GROQ_API_KEY (get one at https://console.groq.com/keys)"
            )

        from groq import Groq  # lazy import; optional dependency, see requirements.txt

        client = Groq(api_key=api_key)
        model = os.environ.get("GROQ_WHISPER_MODEL", DEFAULT_GROQ_MODEL)

        tmp_fd, tmp_name = tempfile.mkstemp(suffix=".ogg", prefix=f"{lecture_id}_groq_")
        os.close(tmp_fd)
        compressed_path = Path(tmp_name)
        chunk_dir = compressed_path.parent / f"{lecture_id}_groq_chunks_{uuid.uuid4().hex[:8]}"
        try:
            audio_extractor.extract_compressed(video_path, compressed_path)

            def transcribe_one_chunk(chunk_path, index, total, prior_tail):
                prompt = vocab_prompt
                if prior_tail:
                    prompt = f"{prompt} {prior_tail}".strip() if prompt else prior_tail
                what = f"[chunk {index}/{total}] " if total > 1 else ""
                print(
                    f"[whisper] {lecture_id}: {what}transcribing via Groq API (model='{model}', "
                    f"language={'pinned ' + forced_language if forced_language else 'auto'})..."
                )
                return self._transcribe_chunk(client, chunk_path, model, forced_language, prompt)

            transcript = transcribe_chunks.transcribe_audio_in_chunks(
                compressed_path,
                GROQ_MAX_UPLOAD_MB * 1024 * 1024,
                transcribe_one_chunk,
                media_probe=FfprobeMediaProbe(),
                tmp_dir=chunk_dir,
            )
            print(
                f"[whisper] {lecture_id}: Groq transcription complete, "
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
            raise CloudTranscriberError(f"Groq transcription failed: {exc}") from exc

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
            raise CloudTranscriberError(f"Groq response missing expected segment data: {exc}") from exc

        try:
            detected_language = transcribe_chunks.sdk_field(response, "language")
        except (KeyError, AttributeError):
            detected_language = None
        detected_language = detected_language or forced_language or ""
        return {"language": detected_language, "segments": segments}
