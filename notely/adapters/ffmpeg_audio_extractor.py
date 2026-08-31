"""AudioExtractor adapter backed by ffmpeg."""

import subprocess
from pathlib import Path

from ..ports import AudioExtractorError


class FfmpegAudioExtractor:
    """Real AudioExtractor, via ffmpeg subprocess calls."""

    def extract_wav(self, video_path: Path, wav_path: Path) -> None:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(wav_path),
        ]
        print(f"[audio] extracting audio: {video_path.name} -> {wav_path.name}")
        self._run(cmd, video_path, "extracting audio")

    def extract_compressed(self, video_path: Path, out_path: Path, bitrate: str = "24k") -> None:
        # Opus at 24kbps is a very small file for speech while staying
        # intelligible: a 90-minute lecture comes out around 16MB,
        # comfortably under Groq's ~25MB free-tier limit, vs. ~170MB for
        # the uncompressed WAV every other backend uses.
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libopus",
            "-b:a",
            bitrate,
            str(out_path),
        ]
        print(f"[audio] extracting compressed audio: {video_path.name} -> {out_path.name} ({bitrate})")
        self._run(cmd, video_path, "compressing audio")

    def _run(self, cmd: list, video_path: Path, action: str) -> None:
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise AudioExtractorError(f"ffmpeg failed {action} from {video_path}: {exc}") from exc
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            raise AudioExtractorError(f"ffmpeg failed {action} from {video_path}:\n{stderr}")
