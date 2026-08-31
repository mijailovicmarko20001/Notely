"""FrameReader adapter backed by cv2 (opencv-python-headless)'s VideoCapture.

cv2 is imported lazily inside sample_frames (not at module top level), same
convention as every other adapter here: offline test collection and
`python -m py_compile` work without the dependency installed.
"""

from pathlib import Path

from ..ports import FrameReaderError, SampledFrame


class Cv2FrameReader:
    """Real FrameReader, via cv2.VideoCapture."""

    def sample_frames(self, video_path: Path, interval: float):
        import cv2

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            cap.release()
            raise FrameReaderError(f"could not open video: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        if fps <= 0:
            cap.release()
            raise FrameReaderError(f"could not read FPS for video: {video_path} (fps={fps})")

        return self._iter_frames(cap, fps, interval)

    @staticmethod
    def _iter_frames(cap, fps: float, interval: float):
        import cv2

        try:
            frame_step = max(1, int(round(interval * fps)))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            frame_idx = 0
            while True:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ok, frame = cap.read()
                if not ok:
                    break
                yield SampledFrame(frame_idx=frame_idx, timestamp=frame_idx / fps, frame=frame)
                frame_idx += frame_step
                if total_frames and frame_idx >= total_frames:
                    break
        finally:
            cap.release()
