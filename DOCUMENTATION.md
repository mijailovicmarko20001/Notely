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
[1] 01_transcribe.py          video ──► output/transcripts/<id>.json        (whisper: mlx GPU, faster-whisper CPU, or opt-in Groq cloud)
[2] 02_extract_slides.py      deck ──► output/slides_extracted/<id>.json    (+ PNG per slide; pypdfium2 / python-pptx)
[3] 03_detect_slide_changes.py video ──► output/frame_events/<id>.json      (OpenCV frame diff + crop)
[4] 04_match_frames_to_slides.py events+slides ──► output/slide_timelines/<id>.json
                                                  + <id>_needs_review.json  (tesseract OCR + TF-IDF + sequential constraint;
                                                                             each timeline entry also keeps its last frame's
                                                                             image path, for stage 6's opt-in vision path)
[5] 05_segment_transcript.py  timeline+transcript ──► output/segmented_transcripts/<id>.json
                                                      (+ duplicate-slide canonicalization; carries the representative
                                                       frame path through to each consolidated slide entry)
[6] 06_generate_notes.py      segments ──► output/notes/<id>.md             (Claude API, concurrent per-slide calls;
                                                                             optional vision input, see §3)
[7] 07_assemble.py            notes/*.md ──► output/study_guide.md          (+ optional --topic-index: one more Claude call)
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
| `config.py` | paths; `.env`-backed settings (`NOTELY_ENV_FILE` override for Docker); `validate_lecture_id()`, the trust-boundary check every filesystem path built from a lecture id goes through |
| `preflight.py` | checks ffmpeg/ffprobe/tesseract(+langs)/soffice/yt-dlp/JS-runtime/API-key/whisper-cache |
| `playlist.py` | playlist URL → ordered entries via `yt-dlp --flat-playlist -J`; rejects non-http(s) URLs before they reach yt-dlp's argv |
| `jobs.py` | the four-lane scheduler (see §2.3); single-lock `JobManager`, `Busy` exception for concurrent-start rejection |
| `progress.py` | per-stage stdout parsers → percent; artifact-existence success table |
| `review.py` | stage-4 review data; manual corrections → timeline rewrite → auto re-run 5–7 |
| `models.py` | Pydantic request models (`SettingsUpdate`, `JobRequest`, `Corrections`, etc.) — malformed request bodies 422 instead of 500ing on a missing dict key |
| `errors.py` | typed domain errors (`ValidationError`/`NotFoundError`/`ConflictError`/`TooLargeError`/`ServerError`) → consistent `{"error": ...}` JSON via a handler in `main.py` |
| `decks.py` | slide-deck upload/merge/dedup logic, streamed to disk in chunks (`MAX_UPLOAD_BYTES`, `NOTELY_MAX_UPLOAD_MB` env), the lecture-matching heuristic |
| `media.py` | guide-PDF subprocess orchestration (lock-guarded regen, atomic rename) and the video-frame preview endpoint's ffmpeg orchestration |
| `routes/` | `settings.py`/`slides.py`/`jobs.py`/`review.py` — the endpoints, one `APIRouter` per resource, mounted under `/api` (this replaced a single 460-line `api.py`) |

**Backend hardening (2026-08-12)** — a grounded audit
(`BACKEND_TODO.md`) found and fixed real gaps once `docker-compose.yml`
started publishing the port beyond localhost: path traversal via
unvalidated lecture ids, no CSRF/DNS-rebinding protection, `.env` line
injection through settings writes, yt-dlp argument injection via
user-supplied URLs, a `JobManager` mutating shared state under
inconsistent locking, and a couple of start/cancel races. `main.py` now
adds `TrustedHostMiddleware`, a CSRF-style Origin/Referer check on
state-changing requests, and opt-in `NOTELY_AUTH_TOKEN` bearer auth for
anyone who widens the Docker port mapping beyond `127.0.0.1`. The old
`api.py` was split into `routes/` + the service/model/error modules above
in the same pass. `tests/webui/` and `tests/test_jobs_scheduler.py` cover
all of it (160 tests total). Full task-by-task rationale in the git log
(`Security:`/`Concurrency:`/`Architecture:`/`Tests:` commits) and the
(now-completed) `BACKEND_TODO.md`.

**Frontend redesign (2026-08-12)** — full design-system pass on
`webui/static/{index.html,style.css,app.js}`: spacing/type/color tokens,
dark mode, a visual drag-to-crop region picker (backed by the new
`GET /api/lectures/{id}/preview-frame` endpoint), workflow-progress
indicators in the nav, a review-item-count badge, consistent loading/
error states, double-submit guards, and accessibility passes
(`aria-live`, `role="log"`, responsive tables/grids, `:focus-visible`).
Full rationale and per-item verification notes in `FRONTEND_TODO.md`
(kept as a standalone document, same pattern as `TODO.md`). One real bug
worth remembering: `button, .button { display: inline-flex }` silently
defeated the browser's `[hidden] { display: none }` rule for every
conditionally-shown element in the app — author `display` rules always
outrank the UA stylesheet regardless of specificity. `element.hidden`
still read `true` in the DOM the whole time, so this only surfaced via an
actual rendered screenshot, not code review or DOM-property checks.
Fixed with a single `[hidden] { display: none !important; }` rule now
sitting near the top of `style.css`, commented so it isn't mistaken for
dead code later.

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

**Live on-slide annotations were an invisible content channel until
2026-08-12** — a real gap the user noticed by watching the recordings
(professors annotating a slide while presenting it — writing, drawing,
circling), not something the earlier audit had surfaced. Before this,
that content was captured nowhere: stage 4 OCRs the actual displayed
frame only to match it to a slide number, then discards the text;
stage 6's prompt was built purely from the deck's own extracted text,
and never sent any image at all. Two gaps, and OCR alone doesn't fix
either well — this course's recordings are hand-drawn ink over a
Zoom-shared PDF, and Tesseract (built for printed text) mangles
handwriting/diagrams badly, the same failure mode already documented for
formula-heavy printed slides.

Fixed with vision, not better OCR: stage 4's collapsed timeline now keeps
each run's *last* event frame (`last_frame_image_path` — annotations
accumulate over a slide's dwell time, so the last frame is the most
complete state); stage 5 carries the chronologically-latest one through
consolidation (a revisited slide may pick up more annotation on its
second visit); stage 6, opt-in via `NOTES_SEND_FRAME_IMAGE` (real added
cost — vision tokens on every slide call, never silently on), sends that
frame to Claude alongside the existing text and embeds it in the note
markdown too, labeled distinctly from the clean deck render. The image
is downscaled to Anthropic's own recommended max dimension before
encoding (frame captures are full video resolution; sending more than
that just wastes bandwidth). `SYSTEM_PROMPT` stayed a static string
(so prompt caching, added earlier the same day, still hits) — the
image-handling rule is phrased as "if you receive an image..." rather
than varying the prompt text per call, so it's correct whether or not a
particular slide has a frame available.

**PDF export.** pandoc/LaTeX was rejected (huge toolchain, fragile with
Serbian + images). Instead: markdown → HTML with math segments protected
from the markdown parser → headless Chrome `--print-to-pdf` with MathJax
(`--virtual-time-budget` lets typesetting finish). Duplicate H1s (assembler
+ note file both emitting `# lectureNN`) produced a blank page per lecture
— assembler now only adds a heading if the note lacks one.

**Cross-lecture topic index** (`07_assemble.py --topic-index`, added
2026-08-11) — the second-LLM-pass idea CLAUDE.md's [7] Assembly section
always left open. Stayed CLI-only and off by default rather than wired
into the web UI/automatic runs: it's one real API call over the *whole*
assembled guide (this course's is ~440K chars/~110K tokens), materially
more expensive than a single lecture's "a few cents." Verified the
insertion logic (TOC → index → lecture content ordering, both-lecture
presence, graceful no-API-key skip, zero calls when the flag is off) end
to end against a scratch project root with the network call stubbed out,
then ran it for real against this course's guide with explicit
go-ahead: 179,367 input / 2,795 output tokens (real tokenization came in
above the rough char/4 estimate — Serbian + LaTeX is denser than plain
English), genuinely useful output (real cross-lecture groupings, working
`#lectureNN` anchor links, a recurring-emphases section that found
material repeated across 7 lectures). `study_guide.md`/`.pdf` now
include it.

**A global forward-jump score floor turned out not to fully fix the known
lecture01 residual.** Added `min_forward_score` (default 0.05, §4.1) as a
genuine guard against near-zero-vs-near-zero forward jumps winning by
`stay_margin` alone — verified safe (zero effect on lecture01's actual
match sequence at that default). But the one *known* spurious match this
was meant to catch (a jump to "slide 71" right after a blank/failed-OCR
frame, score 0.15) sits only ~0.01-0.03 below several genuine transitions
in the same lecture (0.16-0.21, one of them a backward jump the manual
review explicitly confirmed correct) — there's no floor value that
separates the bad match from the good ones. Kept as a forward-looking
guard against a worse (truly near-zero) version of this failure mode on
other lectures; the lecture01 case remains fixed only via its existing
manual correction, documented in that timeline's own `notes` field.

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
| `WHISPER_BACKEND` | `mlx` | this Mac's GPU; Docker/non-Apple uses default `faster-whisper`; `groq` is a third, opt-in cloud option (§5.1) |
| `WHISPER_MLX_REPO` | `mlx-community/whisper-large-v3-turbo` | MLX-converted model |
| `GROQ_API_KEY` / `GROQ_WHISPER_MODEL` | (unset on this machine) | only read when `WHISPER_BACKEND=groq`; see §5.1 for verification status |
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
| Groq upload cap / compressed-audio bitrate | `01_transcribe.py::GROQ_MAX_UPLOAD_MB` / `extract_audio_compressed` | 25 MB / 24kbps Opus (~16MB for a 90-min lecture) |
| Slide render DPI | `02_extract_slides.py` | 150 |
| Frame sample interval | `03` default | 1.5 s |
| Matcher margins | `04` defaults | backward 0.15, stay 0.05, confidence 0.25, min-forward-score 0.05 |
| OCR excerpt length | `04` | 150 chars |
| Frame dHash dedup threshold | `04::DHASH_DEDUP_THRESHOLD` | 3 (of 64 bits) — calibrated against all 22 lectures' real detected events, see §5.1 |
| Min dwell before merge | `05` default | 5.0 s |
| Note/overview token caps | `06` | `MAX_TOKENS = 8192` (both calls) |
| Overview heading text | `06` summary prompt | `## Pregled predavanja` (Serbian; prompt asks model to translate for other languages) |
| Topic index guide-length cap | `07::MAX_GUIDE_CHARS` | 350,000 chars — defensive margin above this course's real ~440K-char/~110K-token guide, well under Claude's context window |
| "Professor's notes:" label | `06::SYSTEM_PROMPT` | English, by design |
| Notes concurrency default | `06` | 4 (`NOTES_CONCURRENCY` env) |
| Send on-screen frame to vision | `06::NOTES_SEND_FRAME_IMAGE` env | off by default — real added cost, see §3's "Live on-slide annotations" entry |
| Frame image max dimension (vision) | `06::FRAME_IMAGE_MAX_DIM` | 1568 px (Anthropic's own recommended long-edge max) |
| Scheduler lane→stage mapping | `webui/jobs.py::_run` | net={0}, cpu={2..5}, api={6}, gpu={1} iff mlx |
| SSE poll interval / event buffer | `webui/jobs.py`, `routes/jobs.py` | 250 ms / 2000 events |
| Server port | `main.py`, compose, README | 8000, bound to `127.0.0.1` only by default in `docker-compose.yml` (widen + set `NOTELY_AUTH_TOKEN` for LAN access) |
| Upload size cap | `webui/config.py::MAX_UPLOAD_BYTES` | 300 MB (`NOTELY_MAX_UPLOAD_MB` env, read at process start) |
| Lecture id format | `webui/config.py::LECTURE_ID_RE` | `^lecture\d{2,}$`, and must exist in `video_urls.json` |
| PDF page setup + styling | `08_export_pdf.py::HTML_TEMPLATE` | A4, 18/16 mm margins, Georgia |
| MathJax source | `08` + Guide tab | jsDelivr CDN — **PDF export and the Study Guide tab's rendered preview both need internet** (raw markdown/math still downloadable offline via `/files/study_guide.md`) |
| Markdown parser (Guide tab) | `static/index.html` | marked.js via jsDelivr CDN — client-side render of the assembled guide, mirrors `08_export_pdf.py`'s math-stashing so LaTeX survives the markdown pass |
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

### 5.1 External transcription services

The transcription backend is already an internal seam — stage 1 dispatches
on `WHISPER_BACKEND` to `transcribe_with_mlx`, `transcribe_with_faster_whisper`,
or (added 2026-08-11) `transcribe_with_groq`, all returning the same shape:

```python
{"language": str, "segments": [{"start": float, "end": float, "text": str}]}
```

**Groq backend: implemented, needs a live-account smoke test before
trusting it.** `WHISPER_BACKEND=groq` + `GROQ_API_KEY`. Sends a compressed
Opus/Ogg encode of the audio (not the uncompressed WAV the local backends
use — Groq's free tier caps uploads around 25MB, which the raw WAV blows
past for anything over ~15 minutes) to Groq's hosted Whisper API. The
request construction and response parsing were checked against the
installed `groq` SDK directly — its `transcriptions.create()` signature
matches what's called, and feeding its response model a synthetic
verbose_json payload confirmed `.language` comes back via plain attribute
access while `.segments` comes back as a list of **plain dicts** (Pydantic
`extra="allow"` doesn't recursively type nested extra fields) — which is
exactly what the dict-or-attribute `_groq_field` helper is built to
handle. What's still genuinely unverified is the live network round-trip:
auth, rate limits, the model name being currently valid, and real audio
producing the same response shape as the synthetic test. Chunking for
lectures whose compressed audio still exceeds the upload cap is not
implemented — it fails with a clear error telling you to use a local
backend for that lecture instead, rather than a half-tested attempt at
re-stitching timestamps across chunks.

Other options worth considering the same way:

| Service | Draw | Watch out |
|---|---|---|
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
  this frame?" — would raise matching accuracy where it's weakest, but is
  real scope (cost, privacy, a new dependency) left for when OCR quality
  is actually the bottleneck.
  ~~An image-hash pre-pass for near-identical frames would be a free local
  win first~~ — done 2026-08-11: `frame_hash`/`hamming_distance` (dHash,
  PIL only) in `04_match_frames_to_slides.py` skip a second OCR call for
  consecutive event frames within a small Hamming distance. Threshold (3
  of 64 bits) calibrated against all 22 lectures' real detected events,
  not guessed — several lectures (17, 21, 22, 10, 06, 14, 18, 20) have
  genuine near-duplicate consecutive events this catches; the closest
  distance between any two frames stage 3 judged genuinely different
  stayed well clear of the threshold across the whole course.
- **Note generation:** ~~prompt caching would cut the repeated
  system-prompt cost~~ — done 2026-08-11: `SYSTEM_PROMPT` (identical
  across every slide, every lecture) goes in as a `cache_control:
  {"type": "ephemeral"}` block; cache read/write tokens surfaced in the
  per-slide and per-lecture logs. Not live-tested against a real API
  call (would spend real credits). ~~Anthropic's Batch API halves cost
  for non-urgent runs~~ — turned out NOT to be a drop-in change: it's
  async/polled, but the web UI's live per-slide progress bar
  (`progress.py`'s `_RE_NOTES` parser) depends on stage 6 streaming
  `[i/N] slide ...` lines as each call finishes. Worth doing as an
  explicit opt-in mode later (fall back to an indeterminate spinner,
  like stages 2/5/7 already do), not a silent default swap.

### 5.2 Other known improvements

- **Pin dependencies** (`pip freeze` → constraints file) before wide
  distribution; loose `>=` pins are a supply-chain and reproducibility risk.
- **Dedupe the pool at merge time** (stage-5 canonicalization fixes the
  data, but a 310-slide haystack would also make stage 4 ~3× faster and
  less ambiguous than the current 936-slide one).
- **amd64 Docker build** is untested (`docker buildx --platform
  linux/amd64`) — needed for Intel/Windows students.
- ~~Web UI guide tab renders raw markdown~~ — fixed 2026-08-11: renders
  client-side (marked.js + MathJax, both CDN) instead of plain text.
- ~~No test suite~~ — added 2026-08-11: `tests/` (pytest, dev-only dep) —
  50 tests covering `webui/progress.py` (stdout parsing + artifact-existence
  checks), `webui/jobs.py` (`build_tasks` + the scheduler's pure
  dependency/claim/settled logic), stage 4's matcher (margin/stay_margin/
  min_forward_score behavior on synthetic cases + `collapse_to_timeline`),
  and stage 5's duplicate-slide canonicalization. Run: `pytest tests/ -q`.
  The pipeline's actual output is still validated by artifact inspection,
  not these tests — they cover the orchestration/algorithm logic around it.
- ~~In-memory job state~~ — investigated 2026-08-11: the transient
  run-status view is lost on restart, but that's cosmetic (artifact-
  existence-based success already makes re-running after a restart
  skip-and-continue for free). Found and fixed a sharper bug behind the
  same symptom instead: every stage wrote its output with plain
  `open(path, "w")`, so a process killed mid-write could leave a
  non-empty-but-corrupt artifact that `artifact_ok` would trust as done,
  silently corrupting resume. All stage 1-7 outputs + the Review tab's
  timeline rewrite now go through a temp-file-then-atomic-rename helper.
- ~~Truncated overviews~~ from the 1024-token era — checked 2026-08-11: all
  22 `output/notes/*.md` files were generated in one batch that already
  post-dates the token-cap fix (mtimes 16:38-17:03 that evening), and a
  heuristic scan found no abrupt overview endings. Moot; no regeneration
  needed.
