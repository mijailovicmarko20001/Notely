# Notely — Technical Documentation

How this system works, how it came to work that way, what is hardcoded, and
where it should go next. Companion to `README.md` (student quick-start) and
`CLAUDE.md` (the original design spec).

---

## 1. What this is

A local, offline-first pipeline that turns recorded lectures (YouTube links)
plus the professor's slide decks into condensed per-slide study notes — the
text that was on each slide *combined with what the professor said while it
was on screen* — assembled into a single markdown study guide and an
optionally exported PDF. A local web UI makes the whole thing runnable by
non-technical students; Docker packaging makes it installable with one
command.

Built and validated against a real course: University of Belgrade
*Hardversko softverska obrada signala* (13E044HSOS), 22 lectures in Serbian,
Zoom-recorded screen captures of PDF slides.

---

## 2. Architecture

### 2.1 Pipeline stages

Each stage is an independent script in `scripts/`, reading the previous
stage's JSON artifact and writing its own. Everything lands on disk, so any
stage can be re-run alone and inspected.

```
[0] 00_fetch_videos.py        YouTube URL ──► input/videos/<id>.mp4         (yt-dlp)
[1] 01_transcribe.py          video ──► output/transcripts/<id>.json        (whisper: mlx GPU or faster-whisper CPU)
[2] 02_extract_slides.py      deck ──► output/slides_extracted/<id>.json    (+ PNG per slide; PyMuPDF / python-pptx)
[3] 03_detect_slide_changes.py video ──► output/frame_events/<id>.json      (OpenCV frame diff + crop)
[4] 04_match_frames_to_slides.py events+slides ──► output/slide_timelines/<id>.json
                                                  + <id>_needs_review.json  (tesseract OCR + TF-IDF + sequential constraint)
[5] 05_segment_transcript.py  timeline+transcript ──► output/segmented_transcripts/<id>.json
                                                      (+ duplicate-slide canonicalization)
[6] 06_generate_notes.py      segments ──► output/notes/<id>.md             (Claude API, concurrent per-slide calls)
[7] 07_assemble.py            notes/*.md ──► output/study_guide.md
[8] 08_export_pdf.py          study_guide.md ──► output/study_guide.pdf     (headless Chrome + MathJax)
```

Conventions every stage follows:

- **Pairing by name**: `lecture01.mp4` ↔ `lecture01.pdf` ↔ `lecture01.json`
  across all stages. The `lectureNN` id is assigned by the UI from confirmed
  playlist order.
- **All paths relative to `PROJECT_ROOT`** (`Path(__file__).parent.parent`)
  — the working directory never matters.
- **Cached-output skip**: existing output → print `[skip]`, exit 0.
  `--force` redoes. Exception: stage 7 exits 1 without `--force` when output
  exists (the web UI always passes `--force` to it).
- **Missing-input skip**: exit 0 with a `[skip]` message — which means exit
  codes alone cannot signal success (see §2.3).
- `argparse` + plain `print()`; heavy imports are lazy so `--help` works
  without any ML dependency installed.

### 2.2 Web UI (`webui/`)

FastAPI + vanilla JS single page (no build step). Key modules:

| Module | Role |
|---|---|
| `config.py` | paths; `.env`-backed settings (`NOTELY_ENV_FILE` override for Docker) |
| `preflight.py` | checks ffmpeg/ffprobe/tesseract(+langs)/soffice/yt-dlp/JS-runtime/API-key/whisper-cache |
| `playlist.py` | playlist URL → ordered entries via `yt-dlp --flat-playlist -J` |
| `jobs.py` | the four-lane scheduler (see §2.3) |
| `progress.py` | per-stage stdout parsers → percent; artifact-existence success table |
| `review.py` | stage-4 review data; manual corrections → timeline rewrite → auto re-run 5–7 |
| `api.py` | all endpoints, incl. deck upload (filename/content pairing + pool mode) and PDF export |

### 2.3 The four-lane scheduler

Stages sort into lanes by the resource they saturate, so different lectures
overlap without competing:

```
net  — stage 0 downloads (bandwidth)      runs ahead of the whole queue
gpu  — stage 1 transcription (Apple GPU)  only when WHISPER_BACKEND=mlx
cpu  — stages 2–5 (cores)                 one at a time; includes stage 1 on the CPU backend
api  — stage 6 note generation (network)  trails behind as lectures finish
```

Stage 7 runs after all lanes drain. Rules learned the hard way:

- **Success = artifact exists**, not exit code (stages exit 0 on missing
  input). Table in `progress.py::stage_artifact`.
- **Failure isolation is per-lecture**: one failed download skips only that
  lecture's remaining stages; the batch continues and the guide assembles
  from whatever succeeded.
- **Never share a lane between unlike stages**: an early version put
  downloads and note generation on one "IO" lane; notes (minutes each)
  blocked downloads, which starved the CPU lane — a convoy that serialized
  the whole pipeline.
- Progress percent is monotonic per task (concurrent stage-6 slides finish
  out of order).
- The UI drives stage scripts individually. `run_pipeline.py` (the original
  CLI orchestrator) cannot pass per-stage flags and is not used by the UI.

### 2.4 Docker

`python:3.12-slim` + apt: ffmpeg, tesseract(+`srp-latn`), libreoffice-impress
(PPTX), chromium (PDF export), deno copied from `denoland/deno:bin`
(yt-dlp's JS runtime). `entrypoint.sh` symlinks `/app/{input,output,.env}`
into the single `/app/data` bind mount; `HF_HOME` puts the whisper model
cache there too, so *everything* persistent lives in `./data` on the host.

Gotcha that shaped this: `python-dotenv`'s `set_key` writes atomically
(temp file + rename), which **replaces a symlinked `.env` with a regular
file inside the container**. Hence `NOTELY_ENV_FILE=/app/data/.env` — the
web UI writes the real file directly; stage scripts read through the
symlink.

Known Docker limitations: `--cookies-from-browser` can't work in a
container (private — not merely unlisted — videos unsupported there), and
transcription is CPU-only inside Docker (no GPU passthrough), at the
mercy of the VM's core allocation.

**Reassessment (2026-08-11):** in practice those two limitations aren't
edge cases, they cut into the pipeline's two most performance/capability-
sensitive stages. Fetching often needs browser cookies (university auth),
and transcription is ~4x faster on the native `mlx` GPU backend than
CPU-only (§3's benchmark table) — both wins require running *outside* a
container, on the host directly. So containerizing doesn't actually buy
isolation for the parts that matter here; it trades away capability the
native run has. The full 22-lecture course in this repo was produced via
the local `.venv` + `uvicorn` path (`data/` — the Docker bind-mount target
— only ever got a one-lecture smoke test, never a full run). Docker mode
still has a place as a lower-friction handoff for students who don't want
to set up Python, GPU drivers, etc. — but it should be presented as the
*reduced-capability, no-setup* option, not the primary/recommended path;
"run locally with `.venv`" is what actually produces the best output on a
machine that has ffmpeg/tesseract/GPU available. Worth deciding explicitly
whether Docker packaging is worth continuing to maintain, or whether a
plain setup script (`brew install ...` + `pip install -r requirements.txt`)
covers the real use case better.

---

## 3. How we got here — decisions and lessons

A condensed engineering log; each item changed the design.

**Deck pairing evolved three times.** Filename numbers (`lecture03.pdf`) →
content suggestions (server fuzzy-matches a deck's first slides against
video titles — useless for this course, whose videos are titled
"Предавање 01_1") → **pool mode**: merge every deck into one combined PDF
shared by all lectures and let stage 4's matching decide per video which
slides were shown. Pool mode works because "deck is a superset of what was
shown" was already the normal case.

**Pool mode exposed massive duplication.** The merged 936-page deck was 66%
duplicate slides (course decks repeat earlier material), and the matcher
smeared one lecture across several copies of the same slide. Fix: stage 5
canonicalizes duplicates by normalized-text hash (`build_canonical_slide_map`)
before consolidation — no re-run of the expensive OCR stages needed.

**Transcription: measure, don't assume.** The full story, all on the same
90s Serbian test clip:

| Attempt | Result |
|---|---|
| whisper *small* | mis-detected Serbian as Bosnian (0.42), mangled vocabulary — rejected |
| *medium*, CPU defaults | correct, 2.0× realtime — the long-time baseline |
| *medium*, int8 + 10 threads | **1.8× — slower.** On Apple Silicon float32 rides the AMX units (Accelerate); int8 can't, extra threads just spin |
| *large-v3-turbo*, CPU | 5.2× realtime, better quality (large encoder, 4-layer decoder) |
| *large-v3-turbo*, **mlx GPU** | **20.1× realtime**, same quality — the shipped default for this machine |

Quality hardening on top: language pinned via `WHISPER_LANGUAGE` (auto-detect
samples 30s and landed on "bs" twice), `initial_prompt` primed with ≤700
chars of the lecture's own slide titles (technical-vocabulary priming),
`vad_filter=True` on the CPU path (whisper hallucinates during silences),
and stage 6 instructs Claude to repair ASR-garbled terms using the slide
text as ground truth.

**yt-dlp needs a JavaScript runtime.** Without one it falls back to legacy
device clients whose stream URLs YouTube now rejects (HTTP 500 walls).
Docker ships deno (yt-dlp's sandboxed default); locally the script
auto-falls-back to `--js-runtimes node` if only Node is present.

**Notes stage lessons.** Per-slide API calls are independent → concurrent
pool (`NOTES_CONCURRENCY`, default 4). `max_tokens` is a disaster brake,
not an output-shaper — a 1024 cap truncated lecture overviews mid-word;
brevity belongs in the prompt, caps sit far above plausible output (8192).
Output format: lecture-language only (no EN/SR mixing), slides numbered
1..N in presentation order (merged-deck numbers are meaningless to a
reader), each note embeds its rendered slide PNG, and each lecture opens
with a generated overview ("Pregled predavanja") including collected
exam-relevant emphases.

**PDF export.** pandoc/LaTeX was rejected (huge toolchain, fragile with
Serbian + images). Instead: markdown → HTML with math segments protected
from the markdown parser → headless Chrome `--print-to-pdf` with MathJax
(`--virtual-time-budget` lets typesetting finish). Duplicate H1s (assembler
+ note file both emitting `# lectureNN`) produced a blank page per lecture
— assembler now only adds a heading if the note lacks one.

**Human review is a feature, not a fallback.** Stage 4 emits
`needs_review.json` (low-confidence matches, never-matched slides, backward
jumps); the UI's Review tab shows frame vs. matched slide side by side, and
corrections rewrite the timeline and auto-re-run stages 5–7. "Most slides
unmatched" is presented as informational — with pooled decks it's the
normal case.

---

## 4. What is hardcoded

### 4.1 Course-/machine-specific configuration (in `.env` — change per course)

| Key | Current value | Why |
|---|---|---|
| `WHISPER_MODEL` | `large-v3-turbo` | benchmark winner |
| `WHISPER_LANGUAGE` | `sr` | auto-detect misfired to "bs"; **unset for other courses or auto-detect** |
| `WHISPER_BACKEND` | `mlx` | this Mac's GPU; Docker/non-Apple uses default `faster-whisper` |
| `WHISPER_MLX_REPO` | `mlx-community/whisper-large-v3-turbo` | MLX-converted model |
| `OCR_LANG` | `srp_latn+eng` | slide language; needs matching tesseract traineddata |
| `NOTES_MODEL` | `claude-sonnet-5` | note generation model |
| `ANTHROPIC_API_KEY` | (secret) | stage 6 only |
| Stage-03 crop | `0.12,0.06,0.63,0.88` | this course's Zoom layout (slide region of frame); UI "Advanced" field, default in `webui/config.py::DEFAULT_STAGE_OPTIONS` |
| Stage-03 threshold | `0.02` | tuned for these recordings (defaults detected almost nothing) |

### 4.2 Hardcoded in code (edit source to change)

| What | Where | Value |
|---|---|---|
| Directory layout (`input/`, `output/`, subdirs) | every script + `webui/config.py` | `PROJECT_ROOT`-relative, no env override |
| Lecture id scheme | UI + all pairing | `lectureNN`, zero-padded 2 digits |
| yt-dlp format selection | `00_fetch_videos.py::FORMAT` | `bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]` |
| Audio extraction params | `01_transcribe.py::extract_audio` | 16 kHz mono PCM WAV |
| Vocabulary-prompt budget | `01_transcribe.py` | `VOCAB_PROMPT_MAX_CHARS = 700` |
| CPU whisper threads/compute defaults | `01_transcribe.py` | 4 / `auto` (env-overridable) |
| Slide render DPI | `02_extract_slides.py` | 150 |
| Frame sample interval | `03` default | 1.5 s |
| Matcher margins | `04` defaults | backward 0.15, stay 0.05, confidence 0.25 |
| OCR excerpt length | `04` | 150 chars |
| Min dwell before merge | `05` default | 5.0 s |
| Note/overview token caps | `06` | `MAX_TOKENS = 8192` (both calls) |
| Overview heading text | `06` summary prompt | `## Pregled predavanja` (Serbian; prompt asks model to translate for other languages) |
| "Professor's notes:" label | `06::SYSTEM_PROMPT` | English, by design |
| Notes concurrency default | `06` | 4 (`NOTES_CONCURRENCY` env) |
| Scheduler lane→stage mapping | `webui/jobs.py::_run` | net={0}, cpu={2..5}, api={6}, gpu={1} iff mlx |
| SSE poll interval / event buffer | `webui/jobs.py`, `api.py` | 250 ms / 2000 events |
| Server port | `main.py`, compose, README | 8000 |
| PDF page setup + styling | `08_export_pdf.py::HTML_TEMPLATE` | A4, 18/16 mm margins, Georgia |
| MathJax source | `08` | jsDelivr CDN — **PDF export needs internet** |
| Chrome binary candidates | `08::CHROME_CANDIDATES` | mac + linux paths |
| Tesseract languages in image | `Dockerfile` | `srp-latn` + `eng` baked in; other languages need an image edit |
| UI whisper-model dropdown | `static/index.html` | small/medium/large-v3 list |

### 4.3 Assumptions baked into the design

- One video per lecture; slides shown mostly in sequential order (the
  matcher's core disambiguation constraint).
- Recordings show slides in a croppable static region.
- Decks are PDFs (PPTX supported via LibreOffice; pool mode is PDF-only).
- Single user, one job at a time (in-memory job state; lost on server
  restart — artifacts on disk are the real state).
- Anthropic is the only LLM provider (stage 6 imports the `anthropic` SDK
  directly).

---

## 5. Improvement points

### 5.1 External transcription services (documented per request)

The transcription backend is already an internal seam — stage 1 dispatches
on `WHISPER_BACKEND` to either `transcribe_with_mlx` or
`transcribe_with_faster_whisper`, both returning the same shape:

```python
{"language": str, "segments": [{"start": float, "end": float, "text": str}]}
```

Adding a cloud backend is therefore one function + one env value. Worth
considering:

| Service | Draw | Watch out |
|---|---|---|
| Groq-hosted whisper | extremely fast + cheap, same model family (quality known) | file-upload size limits → chunk long lectures |
| OpenAI (whisper / gpt-4o-transcribe) | strong quality, simple API | cost per audio-hour; verify Serbian |
| Deepgram / AssemblyAI | word-level timestamps, diarization | Serbian support varies by tier — test first |
| ElevenLabs Scribe | high multilingual accuracy | newer, pricing |

Design considerations for any of them:

- **Privacy is the real trade-off.** Lecture recordings leave the machine.
  The project is deliberately local-first; a cloud backend should be
  opt-in, clearly labeled in the UI, never the default.
- **Timestamps are load-bearing.** Segmentation (stage 5) aligns transcript
  to slide windows; a backend that returns poor/absent timestamps breaks
  the pipeline's core join. Require segment- or word-level times.
- Upload time can rival local GPU transcription (a 40-min lecture is
  ~40–80 MB of audio) — cloud wins mostly for machines *without* a usable
  GPU (i.e., exactly the Docker/student case, which is CPU-bound today).
- Keep the vocabulary-priming idea: most services accept a prompt/keyword
  list; feed them the same slide-title prompt stage 1 already builds.

The same seam-thinking applies to other pipeline organs:

- **OCR (stage 4):** tesseract mangles formula-heavy slides, structurally
  depressing their match confidence. A cloud vision OCR — or skipping text
  entirely and asking a multimodal model "which of these slide images is
  this frame?" — would raise matching accuracy where it's weakest. (An
  image-hash pre-pass for near-identical frames would be a free local win
  first.)
- **Note generation:** Anthropic's Batch API halves cost for non-urgent
  runs; prompt caching would cut the repeated system-prompt cost; both are
  drop-in changes inside stage 6.

### 5.2 Other known improvements

- **Pin dependencies** (`pip freeze` → constraints file) before wide
  distribution; loose `>=` pins are a supply-chain and reproducibility risk.
- **Dedupe the pool at merge time** (stage-5 canonicalization fixes the
  data, but a 310-slide haystack would also make stage 4 ~3× faster and
  less ambiguous than the current 936-slide one).
- **amd64 Docker build** is untested (`docker buildx --platform
  linux/amd64`) — needed for Intel/Windows students.
- **Web UI guide tab renders raw markdown** — no images/math; the PDF is
  the polished view. A small client-side renderer would close that gap.
- **No test suite** — the scheduler and progress parsers have ad-hoc test
  scripts from development (worth formalizing into `tests/`); the pipeline
  itself is validated by artifact inspection.
- **In-memory job state** — a server restart forgets the running job
  (subprocesses die with it). Fine for a single-user tool; persisting the
  queue would allow resume-after-restart.
- **Truncated overviews** from the 1024-token era remain in some lecture
  files until those lectures' notes are regenerated.
