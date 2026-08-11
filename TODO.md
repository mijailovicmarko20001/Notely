# Notely — improvement to-do list

Compiled 2026-08-11 from `DOCUMENTATION.md` §5 (already-documented roadmap)
plus a fresh pass over the repo. Check items off as they land; when the
roadmap shifts materially, update `DOCUMENTATION.md` §5 too — this file is
the working checklist, that one is the record of *why*.

## P0 — housekeeping / risk

- [x] **No git repo at all.** Done 2026-08-11: `git init` + `.gitignore`
      (`.venv/`, `data/`, `output/`, `input/videos/`, `input/slides/`,
      `*.pyc`, `__pycache__/`, `.env`, `data/.env`, `.DS_Store`, real
      `input/video_urls.json`/`lectures.json`) + first commit. Verified
      no secrets/binaries staged before committing.
- [x] **`.env` secret risk.** Covered by the `.gitignore` above — confirmed
      `.env`/`data/.env` excluded before the first `git add`.
- [x] **`.env.example` was stale.** Rewritten to cover every env var the
      scripts/webui actually read (`WHISPER_LANGUAGE`, `WHISPER_BACKEND`,
      `WHISPER_MLX_REPO`, `WHISPER_COMPUTE`/`WHISPER_CPU_THREADS`,
      `OCR_LANG`, `NOTES_CONCURRENCY`, `NOTES_EMBED_IMAGES`), with defaults
      matching the code and notes on which are course-specific tuning.
- [x] **Two divergent data trees.** Resolved by decision (2026-08-11):
      `output/`/`input/` (top-level) is the real, complete course run and
      stays canonical; `data/`'s partial contents were only ever a
      one-lecture Docker smoke test. Rather than migrating data into
      `data/`, documented *why* in `DOCUMENTATION.md` §2.4 — Docker mode's
      two limitations (no `--cookies-from-browser`, CPU-only transcription)
      cut into the pipeline's two most capability-sensitive stages, so the
      native `.venv` path is the one that actually produced this repo's
      output, not Docker. README's Docker section now says so explicitly
      instead of implying Docker is the full-power path. Open follow-up:
      **decide whether Docker packaging is worth continuing to maintain
      given this**, or whether a plain setup script covers the real
      use case better (added as a P2 item below). Separately: `output/`
      (1.3 GB) is already gitignored and not required by Docker mode, so it
      can be moved outside the project folder for tidiness whenever you
      want — no destination decided yet, so left as-is for now.
- [x] **Human spot-check.** Confirmed already done by the user
      (2026-08-11) — generated notes were checked against the source
      video before trusting the batch run.
- [x] **Pin dependencies.** Added `requirements-lock.txt` (exact `pip
      freeze` of the validated dev `.venv`, macOS arm64) alongside the
      existing loose `requirements.txt`; README's dev-mode section now
      mentions it. Not swapped in as the default install path — that'd be
      a bigger call (breaks cross-platform Docker builds, which need the
      loose pins) — flagging that trade-off rather than deciding it here.

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

- [ ] **Decide whether Docker packaging is worth continuing to maintain.**
      Its two limitations (no browser-cookie auth, CPU-only transcription)
      hit the pipeline's most capability-sensitive stages — this repo's
      actual output was produced natively, not via Docker (see
      `DOCUMENTATION.md` §2.4). Options: keep it as an explicitly
      lower-power/no-setup on-ramp for non-technical students (current
      framing, already updated in the README); or drop it in favor of a
      plain setup script (`brew install ffmpeg tesseract tesseract-lang` +
      `pip install -r requirements.txt`) if it's not pulling its weight.

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
