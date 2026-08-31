"""Contract test for notely.ports.FrameReader (Phase 3, port 9 of 9 --
the last one).

Unlike the other 8 ports, cv2.VideoCapture is a stateful object (open,
seek, read, release), not a one-shot call, and the frame-stepping logic
(interval -> frame index, via FPS) is business logic stage 3 owns -- so
this port's sample_frames() does that stepping itself and returns an
iterator of SampledFrame, meaning FakeFrameReader just needs a canned list
rather than reimplementing the stepping. Real frames are numpy arrays
here (via the real, already-installed numpy dependency) -- no video file
fixture needed, small synthetic arrays exercise the real cv2/numpy
processing stage 3 does on each frame."""

import numpy as np
import pytest

from notely.adapters.cv2_frame_reader import Cv2FrameReader
from notely.ports import FrameReader, FrameReaderError, SampledFrame
from tests.fakes import FakeFrameReader


def _frame(color=128):
    return np.full((48, 64, 3), color, dtype=np.uint8)


def test_both_implementations_satisfy_the_protocol():
    assert isinstance(FakeFrameReader(), FrameReader)
    assert isinstance(Cv2FrameReader(), FrameReader)


def test_fake_yields_canned_frames_in_order():
    frames = [
        SampledFrame(frame_idx=0, timestamp=0.0, frame=_frame(0)),
        SampledFrame(frame_idx=45, timestamp=1.5, frame=_frame(255)),
    ]
    fake = FakeFrameReader(frames=frames)

    result = list(fake.sample_frames("lecture01.mp4", interval=1.5))

    assert result == frames


def test_fake_records_every_call():
    fake = FakeFrameReader()
    list(fake.sample_frames("lecture01.mp4", interval=1.5))
    list(fake.sample_frames("lecture02.mp4", interval=2.0))
    assert fake.calls == [("lecture01.mp4", 1.5), ("lecture02.mp4", 2.0)]


def test_fake_raises_configured_error_instead_of_yielding():
    fake = FakeFrameReader(error=FrameReaderError("could not open video"))
    with pytest.raises(FrameReaderError, match="could not open video"):
        fake.sample_frames("lecture01.mp4", interval=1.5)


def test_real_adapter_raises_immediately_for_a_nonexistent_video(tmp_path):
    # sample_frames itself (not the returned iterator) must raise -- this
    # is not a generator function, so the failure surfaces at the call
    # site, not on first iteration.
    with pytest.raises(FrameReaderError, match="could not open"):
        Cv2FrameReader().sample_frames(tmp_path / "nonexistent.mp4", interval=1.5)


def test_real_adapter_raises_for_a_file_that_is_not_a_video(tmp_path):
    not_a_video = tmp_path / "not_a_video.mp4"
    not_a_video.write_bytes(b"this is definitely not video data")

    with pytest.raises(FrameReaderError):
        Cv2FrameReader().sample_frames(not_a_video, interval=1.5)
