# CLAUDE.md — Lecture Video + Slide → Study Notes Pipeline

## What we're building

A local, offline pipeline that takes recorded lecture videos + the professor's
slide decks for a university course, and produces condensed, per-slide study
notes that combine:

- the text that was actually on each slide, and
- what the professor *said* while that slide was on screen (examples,
  emphasis, "this will be on the exam" asides, corrections, etc.)

The end goal is a set of markdown notes per lecture that someone can read in
20–30 minutes instead of sitting through the full recording.

This is a **batch, offline** tool. No real-time processing, no web app —
just scripts run one after another over a folder of inputs.

---

## Inputs

- `input/video_urls.json` — one YouTube URL per lecture (see [0] below).
  **The videos are not local files** — they're shared as YouTube links that
  aren't publicly listed/searchable. They need to be fetched first.
- `input/slides/` — one slide deck per lecture (pptx or pdf), ideally named
  so it's obvious which deck pairs with which video (e.g. `lecture03.pptx`
  pairs with the `lecture03` entry in `video_urls.json`)

Assume **one slide deck per video**, and assume slides within a lecture are
shown roughly **in sequential order** (professors occasionally jump back,
but mostly go forward). This assumption is important — use it later as a
disambiguation heuristic, don't discard it.

`input/videos/` still exists as a directory, but it's now populated by
running stage [0] rather than by manually dropping files in.

---

## Pipeline overview

```
youtube_url ──► [0] fetch (yt-dlp) ──────► video (local mp4)

video ──► [1] transcription ──────────────► transcript.json (timestamped)
slides ─► [2] slide text extraction ──────► slides.json

video ──► [3] slide-change detection ─────► frame_events.json (timestamp + frame image)
frame_events + slides ─► [4] frame-to-slide matching ─► slide_timeline.json
                                                          (slide_number → [start, end])

slide_timeline + transcript ─► [5] segmentation ─► per_slide_transcript.json

slides + per_slide_transcript ─► [6] note generation (LLM) ─► notes/lectureNN.md

notes/*.md ─► [7] assembly ─► final study guide
```

Each stage reads the previous stage's JSON output and writes its own. Keep
every intermediate artifact on disk (don't pipe everything in memory) — this
makes it possible to re-run just one stage, inspect intermediate output, and
debug the (error-prone) alignment step without re-transcribing every time.

---

## Suggested directory structure

```
project/
  input/
    video_urls.json         # {"lecture01": "https://youtu.be/...", ...}
    videos/                 # populated by stage [0], not manually
    slides/
  output/
    transcripts/          # [1]
    slides_extracted/      # [2]
    frame_events/          # [3]
    slide_timelines/       # [4]
    segmented_transcripts/ # [5]
    notes/                 # [6]
    study_guide.md          # [7]
  scripts/
    00_fetch_videos.py
    01_transcribe.py
    02_extract_slides.py
    03_detect_slide_changes.py
    04_match_frames_to_slides.py
    05_segment_transcript.py
    06_generate_notes.py
    07_assemble.py
    run_pipeline.py          # orchestrator, calls 0-7 for a given lecture
  .env                       # ANTHROPIC_API_KEY, never commit; also
                              # YOUTUBE_COOKIES_BROWSER if needed, see [0]
  CLAUDE.md
```

---

## Build order

Build and test each stage independently, on **one lecture only**, before
chaining them together or running on the full course. Stage 4 (matching) is
the highest-risk step — get a human to sanity-check its output before
trusting the rest of the pipeline on 12 hours of video.

1. Slide text extraction (no video needed, fastest to get working)
2. Fetching one video from its YouTube link (get this working and confirm
   the download is complete/uncorrupted before building anything downstream)
3. Transcription
4. Slide-change detection in video
5. Frame-to-slide matching
6. Transcript segmentation by slide
7. Note generation via LLM
8. End-to-end orchestration + assembly
9. Only then: batch-run across all lectures

---

## Stage details

### [0] Fetch videos from YouTube

Goal: turn each unlisted/private YouTube link into a local video file that
stages [1] and [3] can work with.

Use `yt-dlp` (actively maintained; more reliable than `youtube-dl` at this
point) — install via pip, not apt, to get a recent version.

**First, figure out which kind of "not public" these are** — it changes the
approach:

- **Unlisted**: the video doesn't show up in search or on the channel, but
  anyone with the exact link can view it without logging in. `yt-dlp` handles
  this with zero extra config — just needs the URL.
- **Private**: the video is restricted to specific Google accounts the
  professor has explicitly granted access to (e.g. via university G Suite).
  `yt-dlp` will fail here unless it can authenticate as one of those
  accounts. In that case, pass browser cookies from a browser session
  that's already logged into that account:
  `yt-dlp --cookies-from-browser chrome <url>` (or `firefox`, etc.) so
  `yt-dlp` can present valid auth cookies to YouTube.

If you're not sure which situation applies, just try without cookies first
— if `yt-dlp` throws a "private video" or "sign in" error, that tells you
it's the second case.

**Config file** (`input/video_urls.json`):
```json
{
  "lecture01": "https://youtu.be/XXXXXXXXXXX",
  "lecture02": "https://youtu.be/YYYYYYYYYYY"
}
```

**Download command shape:**
```bash
yt-dlp -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]" \
  -o "input/videos/%(id)s.%(ext)s" \
  --cookies-from-browser chrome \
  <url>
```
Use `input/video_urls.json`'s key (e.g. `lecture01`) as the output filename
rather than the raw YouTube video ID, so downstream stages can match videos
to slide decks by name.

- Download the full video (not just audio) — stage [3] needs the visual
  frames to detect slide changes. Audio-only would only work if you're
  willing to skip visual slide-matching entirely and rely purely on some
  other timing signal, which we're not doing here.
- After each download, verify the file is playable/non-empty before moving
  on (e.g. check duration with `ffprobe`) — partial or failed downloads
  should stop the pipeline for that lecture rather than silently feeding a
  broken file into transcription.
- Cache what's already downloaded — don't re-fetch a video that's already
  present locally, since these can be long files and re-downloading 12
  hours of lecture repeatedly wastes time and bandwidth.

**A note on this step, worth being deliberate about:** downloading videos
from YouTube technically runs against YouTube's Terms of Service, regardless
of the video's visibility setting. This is a common and low-friction
practice for personal study use of your own course materials, but it's
worth being aware of, and the downloaded files/notes shouldn't be
redistributed beyond personal use.

### [1] Transcription

Use `faster-whisper` (local, no API key needed, much faster than vanilla
whisper on CPU). Fall back to `whisper.cpp` if the environment has no
decent GPU and faster-whisper is too slow.

- Extract audio from video with ffmpeg first (`-vn -acodec pcm_s16le`).
- Transcribe with word- or segment-level timestamps (`word_timestamps=True`
  if using faster-whisper).
- Save output as JSON: list of `{start, end, text}` segments.
- Detect the lecture's language automatically rather than hardcoding it.

### [2] Slide text extraction

- `.pptx` → `python-pptx`: pull all text frames per slide (titles, bullets,
  and speaker notes if present — speaker notes are often gold for context).
- `.pdf` (slides exported as PDF) → `pypdfium2` (permissively licensed —
  BSD-3/Apache-2.0; PyMuPDF/`fitz` was the original choice but is AGPL-3.0,
  a real concern for a tool that runs as a network service, so it was
  swapped out before open-sourcing) or `pdfplumber`.
- Also render each slide to a PNG image at this stage (needed for stage 4) —
  `pypdfium2` can do this directly for PDFs; for pptx, either convert to PDF
  first with `libreoffice --headless --convert-to pdf`, or render via
  COM/other tooling if on Windows.
- Save as JSON: list of `{slide_number, title, body_text, notes_text,
  image_path}`.

### [3] Slide-change detection in video

Goal: find the timestamps where the displayed slide changes.

- Sample video frames at a coarse interval (e.g. every 1–2 seconds) with
  OpenCV — no need to process every frame.
- Compute a similarity metric between consecutive sampled frames (structural
  similarity / SSIM, or simple pixel-diff on a downscaled grayscale frame).
  A sharp drop in similarity = likely slide change.
- Alternative/complement: `PySceneDetect`'s content detector, which is built
  for exactly this kind of cut detection and may need less tuning.
- For each detected change, save the timestamp and the actual frame image
  (crop to the screen-share region if the recording includes a webcam
  picture-in-picture — this may need a one-off manual crop region per
  video, since layouts often differ recording to recording).
- Output: list of `{timestamp, frame_image_path}`.

Expect noise here (a professor's cursor or a slide animation can trigger a
false positive). That's fine — stage 4's matching step will absorb some of
this, and you can also de-duplicate consecutive events that match the same
slide.

### [4] Frame-to-slide matching

Goal: for each detected frame-change event, figure out which slide number
it actually is.

- OCR each captured frame with `pytesseract`.
- Compare the OCR'd text against each slide's extracted text (stage 2) using
  a text similarity measure — TF-IDF cosine similarity is a reasonable
  starting point; sentence-embedding similarity (e.g. via
  `sentence-transformers`) is more robust if OCR is noisy.
- **Use the sequential-order assumption as a constraint**: since slides are
  shown mostly in order, prefer matches that keep slide numbers
  non-decreasing over time, and only allow a "jump back" if the similarity
  score for the jump is much stronger than staying in sequence. This alone
  will fix most OCR/ambiguity errors.
- Collapse consecutive events that resolve to the same slide.
- Output: `slide_timeline.json` — `{slide_number: [start_time, end_time]}`
  for every slide in the deck.
- Log a confidence score per match. Flag anything below a threshold (e.g.
  low text similarity, or a slide that never got matched at all) into a
  `needs_review.json` so it surfaces instead of silently producing bad notes.

### [5] Transcript segmentation

- For each slide's `[start_time, end_time]` window from stage 4, collect all
  transcript segments (stage 1) that fall inside it.
- Handle edge cases: a transcript segment straddling a boundary (split it or
  assign by majority overlap), and slides with very short or zero dwell
  time (the professor flicked past it — merge its transcript into the
  neighboring slide rather than producing an empty note).
- Output: per-slide `{slide_number, slide_text, transcript_text}`.

### [6] Note generation (LLM)

- For each slide, call the Claude API with both the slide's text and its
  matched transcript chunk.
- Prompt goals — be explicit about these, since default summarization tends
  to just restate the slide bullets:
  - Preserve anything the professor said that *isn't* on the slide:
    examples, clarifications, corrections, exam hints, edge cases.
  - Keep definitions and formulas exact, don't paraphrase them loosely.
  - Output structured markdown: slide title as heading, key points as
    bullets, a short "professor's notes" subsection for verbal-only content.
- Batch requests per lecture; write output as `notes/lectureNN.md`.
- Store the raw prompt/response pairing alongside the note file for easier
  debugging if a note looks wrong.

### [7] Assembly

- Concatenate per-lecture note files into a single study guide, in course
  order.
- Optionally: a second LLM pass across the *assembled* guide to build a
  topic index or cross-lecture summary, since exam-relevant connections
  often span multiple lectures.

---

## Suggested stack

- Python 3.11+
- `yt-dlp` — fetching video from YouTube links
- `ffprobe` (ships with ffmpeg) — verifying downloaded video integrity
- `faster-whisper` — transcription
- `python-pptx`, `pypdfium2` / `pdfplumber` — slide text + rendering
- `pypdf` — PDF page merging (webui slide-pool dedup)
- `opencv-python`, optionally `scenedetect` — slide-change detection
- `pytesseract` (+ tesseract binary installed via apt) — OCR
- `sentence-transformers` or `scikit-learn` (TF-IDF) — text similarity
- `anthropic` Python SDK — note generation
- `ffmpeg` — audio extraction, format normalization
- `python-dotenv` — load `ANTHROPIC_API_KEY` from `.env`

---

## Validation

- After stage 4, manually check the `slide_timeline.json` for at least one
  full lecture against the actual video before trusting it further —
  misalignment here silently corrupts every downstream note.
- After stage 6, spot-check a handful of generated notes against the source
  video for at least the first lecture processed.
- Don't fully automate the first end-to-end run across all 12 hours — run
  one lecture, check it, then batch the rest.

## Explicit non-goals

- No real-time/streaming processing.
- No fancy ML model for slide matching — OCR + text similarity + the
  sequential-order constraint should comfortably clear 90%+ accuracy for a
  normal lecture deck; don't over-build this part.
- No public redistribution of generated notes — this is for personal study
  use only, since the source materials aren't ours to redistribute.
