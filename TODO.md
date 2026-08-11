# Notely — improvement to-do list

Compiled 2026-08-11 from `DOCUMENTATION.md` §5 (already-documented roadmap)
plus a fresh pass over the repo. Check items off as they land; when the
roadmap shifts materially, update `DOCUMENTATION.md` §5 too — this file is
the working checklist, that one is the record of *why*.

## P0 — housekeeping / risk

- [ ] **No git repo at all.** Everything here is unversioned — no history,
      no rollback, no diffing. `git init`, add a `.gitignore`
      (`.venv/`, `data/`, `output/`, `input/videos/`, `input/slides/` (large
      binaries — decide if these belong in git or stay local-only), `*.pyc`,
      `__pycache__/`, `.env`, `.DS_Store`), then first commit.
- [ ] **`.env` currently holds a live `ANTHROPIC_API_KEY` in plaintext** in a
      project with no `.gitignore` yet — do the gitignore *before* `git add .`
      so the key never enters history by accident.
- [ ] **`.env.example` is stale.** It's missing `WHISPER_LANGUAGE`,
      `WHISPER_BACKEND`, `WHISPER_MLX_REPO`, `OCR_LANG` — all four are read
      by the scripts/webui and set in the real `.env`. Its `WHISPER_MODEL`
      default (`small`) also contradicts the benchmarked recommendation
      (`large-v3-turbo`) recorded in `DOCUMENTATION.md` §4.1. A student
      copying `.env.example` today gets a worse, incomplete config.
- [ ] **Two divergent data trees exist**: top-level `input/`/`output/`
      (fully populated — all 22 lectures processed, `study_guide.md` +
      `.pdf` present) vs `data/input/`/`data/output/` (the Docker
      bind-mount target — only `lecture01.mp4` + transcripts, nothing else).
      Right now it's unclear whether `data/` is a stale partial test run or
      the "real" home going forward. Decide and either delete `data/`'s
      partial contents or migrate/re-run the full course through
      `docker compose up` so the two trees don't silently disagree.
- [ ] **Confirm the pending human spot-check actually happened.** Per
      `CLAUDE.md`'s validation section and prior session notes, a manual
      check of `slide_timeline.json` + generated notes against the real
      video was still outstanding as of the last update. All 22 lectures'
      notes now exist — worth explicitly confirming at least lecture01 and
      one or two others were checked before trusting the rest of the batch.
- [ ] **Pin dependencies.** `requirements.txt` uses loose `>=` pins
      throughout — reproducibility/supply-chain risk before any wider
      distribution. Freeze into a constraints file.

## P1 — correctness & quality (documented gaps worth closing)

- [ ] **Stage 4 has no score floor on forward jumps** — only backward jumps
      are margin-gated. This already produced two known spurious matches on
      lecture01 (near-zero-score forward jumps at 633s/2137s, manually
      fixed). Add an absolute-score floor for forward jumps too, then
      re-check whether it recurs on other lectures' `needs_review.json`.
- [ ] **Regenerate any lecture notes from before the token-cap fix.** Early
      overviews were generated with a 1024-token cap that truncated
      mid-word; the fix (2048, later 8192) landed 2026-08-11. Grep
      `output/notes/*.md` for overviews that end abruptly and re-run stage 6
      for just those lectures.
- [ ] **Dedupe the pooled slide deck at merge time**, not just after OCR.
      Stage 5 already canonicalizes duplicates by text-hash post-hoc, but
      the merged deck stage 4 actually searches is still 936 pages (66%
      duplicate) instead of the ~310 unique slides — deduping earlier makes
      stage 4 ~3x faster and less ambiguous for free.
- [ ] **Study Guide tab renders raw markdown** in the web UI — no images,
      no math. The exported PDF is the only polished view. A small
      client-side markdown+MathJax renderer would close that gap for
      students who don't want to download the PDF.
- [ ] **No test suite.** The scheduler (`webui/jobs.py`) and progress
      parsers (`webui/progress.py`) have ad-hoc dev scripts from
      debugging — worth formalizing into `tests/` so scheduler/lane logic
      doesn't silently regress.
- [ ] **In-memory job state.** A server restart forgets the running job
      (subprocesses die with it) — fine for single-user local use, but
      annoying mid-batch-run. Consider persisting the queue/lane state so a
      restart can resume rather than requiring a manual re-kick.
- [ ] **amd64 Docker build is untested** (`docker buildx --platform
      linux/amd64 build .`) — needed before handing this to Intel/Windows
      students; only Apple Silicon has been validated so far.

## P2 — larger, optional roadmap items

- [ ] **Opt-in cloud transcription backend** (Groq-hosted Whisper, OpenAI,
      Deepgram/AssemblyAI, ElevenLabs Scribe) for machines without a usable
      GPU — the seam already exists (`WHISPER_BACKEND` dispatch in
      `01_transcribe.py` returns a uniform `{language, segments}` shape).
      Must stay opt-in and clearly labeled — recordings leaving the machine
      is a real privacy trade-off for a project that's deliberately
      local-first. Require segment/word-level timestamps (segmentation
      depends on them) and keep the vocabulary-priming trick.
- [ ] **Better OCR/matching for formula-heavy slides.** Tesseract mangles
      math, which structurally depresses match confidence exactly where
      it's weakest. Options: a cloud vision OCR, or skip text entirely and
      ask a multimodal model "which slide image is this frame" directly.
      Cheap first step: an image-hash pre-pass to collapse near-identical
      frames before OCR runs at all.
- [ ] **Anthropic Batch API + prompt caching for stage 6** — batch halves
      cost for non-urgent runs; caching the repeated system prompt cuts
      per-slide cost further. Both are drop-in changes inside
      `06_generate_notes.py`.
- [ ] **Cross-lecture topic index** — the `TODO` already in
      `07_assemble.py`: an optional second LLM pass over the *assembled*
      guide to surface connections that span multiple lectures (common
      exam material).

---
Sources: `DOCUMENTATION.md` §5 (pre-existing roadmap), repo inspection
2026-08-11 (no `.git`, no `.gitignore`, stale `.env.example`, divergent
`data/` vs top-level trees).
