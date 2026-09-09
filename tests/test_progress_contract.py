"""Contract test: webui/progress.py's _RE_* regexes vs. the literal stdout
lines the stage scripts (and, for stage 0/1's mlx path, the third-party
libraries they shell out to) actually emit.

tests/test_progress.py already exercises parse_line's branching logic with
hand-typed lines that merely satisfy each regex; that's not quite the same
guarantee. This file's job is narrower and more literal: each line below is
copied from -- and comment-cited to -- the exact producer, so a future
change to that producer's format string is caught here even if the change
happens to still satisfy the regex's own hand-typed test case. If a stage
script's print format changes, update the copied line in this file in the
same commit and re-derive the asserted percentage by hand."""

from webui import progress


def test_stage0_ytdlp_download_line():
    # yt-dlp's own "--newline --progress" output format (not ours to
    # control) -- see scripts/00_fetch_videos.py:127-129's comment. A real
    # line also carries size/speed/ETA after the percent; _RE_YTDLP only
    # anchors on the leading "[download]  NN.N%".
    line = "[download]  63.7% of  348.21MiB at    5.44MiB/s ETA 00:22"
    assert progress.parse_line(0, line, {}) == 0.637


def test_stage1_faster_whisper_segment_line():
    # scripts/01_transcribe.py:395 -- f"  [{seg.start:8.2f} -> {seg.end:8.2f}] {text}"
    seg_start, seg_end, text = 483.10, 491.75, "and that concludes today's example."
    line = f"  [{seg_start:8.2f} -> {seg_end:8.2f}] {text}"
    pct = progress.parse_line(1, line, {"video_duration": 600.0})
    assert pct == 491.75 / 600.0


def test_stage1_mlx_whisper_segment_line():
    # mlx-whisper's own verbose=True output format, documented at
    # scripts/01_transcribe.py:213: "[MM:SS.mmm --> MM:SS.mmm] text"
    line = "[08:03.100 --> 08:11.750] and that concludes today's example."
    pct = progress.parse_line(1, line, {"video_duration": 600.0})
    assert pct == (8 * 60 + 11.75) / 600.0


def test_stage1_mlx_whisper_segment_line_past_one_hour():
    # same format, hour component present -- scripts/01_transcribe.py:213
    line = "[1:00:08.100 --> 1:00:11.750] the second hour begins here."
    pct = progress.parse_line(1, line, {"video_duration": 7200.0})
    assert pct == (3600 + 11.75) / 7200.0


def test_stage3_slide_change_event_line():
    # scripts/03_detect_slide_changes.py:182 --
    # f"  [event {event_index:03d}] t={timestamp:8.2f}s ({tag}) -> {image_name}"
    event_index, timestamp, tag, image_name = 12, 245.50, "cut", "event_012.png"
    line = f"  [event {event_index:03d}] t={timestamp:8.2f}s ({tag}) -> {image_name}"
    pct = progress.parse_line(3, line, {"video_duration": 500.0})
    assert pct == 245.50 / 500.0


def test_stage4_ocr_progress_line():
    # scripts/04_match_frames_to_slides.py:833 --
    # f"  [ocr {i + 1}/{len(events)}] {frame_path.name}"
    line = "  [ocr 3/12] event_003.png"
    assert progress.parse_line(4, line, {}) == 3 / 12


def test_stage6_notes_progress_line():
    # scripts/06_generate_notes.py:414-415 --
    # f"  [{index}/{total}] slide {slide_number}: ok{frame_note} "
    # f"(input={usage['input_tokens']} output={usage['output_tokens']} tokens{cache_note})"
    index, total, slide_number = 7, 20, 7
    line = f"  [{index}/{total}] slide {slide_number}: ok (input=1200 output=300 tokens)"
    assert progress.parse_line(6, line, {}) == 7 / 20


def test_stage6_example_confirmation_progress_line():
    # scripts/06_generate_notes.py:636 --
    # f"  [{index}/{total}] example @{fmt_ts(candidate['timestamp'])}: {status} ({verdict['kind']})"
    index, total = 2, 5
    line = f"  [{index}/{total}] example @00:12: confirmed (whiteboard)"
    assert progress.parse_line(6, line, {}) == 2 / 5


def test_stage9_lecture_essentials_progress_line():
    # notely/pipeline/essentials.py::process_lecture_essentials --
    # f"  [{index}/{total}] lecture {lecture_id}: ok (input=... output=... tokens)"
    index, total, lecture_id = 1, 3, "lecture01"
    line = f"  [{index}/{total}] lecture {lecture_id}: ok (input=400 output=120 tokens)"
    assert progress.parse_line(9, line, {}) == 1 / 3
