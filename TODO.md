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

- [x] **Stage 4 forward-jump score floor.** Added `--min-forward-score`
      (default 0.05) to `04_match_frames_to_slides.py`, plumbed through the
      web UI. Verified safe against lecture01's real data (zero change to
      its match sequence at the default). **Turned out not to fully fix
      the known lecture01 case** — the one documented spurious match
      (jump to "slide 71", score 0.15) scores within ~0.01-0.03 of several
      *genuine* transitions in the same lecture (0.16-0.21); no flat floor
      separates them without collateral damage. Kept as a real, tested
      guard against worse (near-zero) versions of this failure on other
      lectures — see `DOCUMENTATION.md` §3 for the full investigation.
      lecture01's known case remains fixed only via its existing manual
      timeline correction.
- [ ] **Regenerate any lecture notes from before the token-cap fix.** Early
      overviews were generated with a 1024-token cap that truncated
      mid-word; the fix (2048, later 8192) landed 2026-08-11. Grep
      `output/notes/*.md` for overviews that end abruptly and re-run stage 6
      for just those lectures.
- [x] **Dedupe the pooled slide deck at merge time.** `/api/slides/upload-pool`
      now skips pages whose normalized text exactly matches one already
      kept (empty-text/image-only pages are always kept, never deduped
      against each other, to avoid collapsing visually distinct slides on
      an empty-string hash match). Verified read-only against the real
      pool PDFs: 312 raw pages -> 310 kept, 2 genuine duplicates found, no
      over-merging. Note: the currently-stored pool files total only 312
      pages, not the previously-documented 936/310 split — that number was
      from an earlier/larger pool upload session; worth a sanity check
      next time a full pool is re-uploaded.
- [x] **Study Guide tab renders raw markdown.** Now renders client-side via
      marked.js + MathJax (both CDN, same jsDelivr/MathJax combo stage 08
      already uses for the PDF — no new offline trade-off). Mirrors
      `08_export_pdf.py`'s math-stashing trick so LaTeX underscores survive
      the markdown pass, and rewrites relative image/link paths against
      `/files/` (where `output/` is mounted). Verified: the regex logic
      against real `study_guide.md` content (Node, isolated from the CDN
      dependency) and a live server smoke test (`/api/guide`, `/static/app.js`,
      and a sample slide image all 200).
- [x] **No test suite.** Added `tests/` (pytest, dev-only dependency —
      `.venv/bin/pytest tests/ -q`): 50 tests across `webui/progress.py`,
      `webui/jobs.py`'s `build_tasks` + scheduler helpers, stage 4's
      matcher (including the new `min_forward_score` behavior, on synthetic
      cases hand-verified against the algorithm), stage 4's
      `collapse_to_timeline`, and stage 5's duplicate-slide
      canonicalization. All passing.
- [x] **In-memory job state — investigated, found a sharper underlying bug
      and fixed that instead.** The transient run-status view (which task is
      running, live log) is genuinely lost on restart, but that's mostly
      cosmetic: success is judged by artifact existence
      (`progress.py::artifact_ok`), so re-clicking Run after a restart
      already skips completed stages and continues — no queue-persistence
      layer needed for that part. The real bug: every stage wrote its
      output with a plain `open(path, "w")`, which truncates immediately.
      A process killed mid-write (the exact scenario a restart implies)
      could leave a non-empty-but-corrupt artifact that `artifact_ok`
      (exists + size > 0) would trust as done — the next run would skip
      re-generating it, and the following stage would crash loading invalid
      JSON. Fixed by writing every gating artifact (stages 1-7's JSON/MD
      outputs, plus the Review tab's timeline rewrite) via a temp file +
      atomic rename, so a kill can only ever leave the *old* artifact or
      the *complete new one*, never a partial one. Verified the helpers in
      isolation (content correctness, overwrite, nested dirs, zero leftover
      temp files) — did not force-rerun any real stage against production
      output, since that would have destroyed lecture01's manually
      corrected timeline.
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
