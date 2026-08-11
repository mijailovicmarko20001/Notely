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
- [x] **Regenerate any lecture notes from before the token-cap fix — turned
      out to be moot.** Checked file mtimes: all 22 `output/notes/*.md`
      files were generated in one batch on 2026-08-11 16:38-17:03, which
      post-dates the token-cap fix. Also ran a heuristic scan (every
      lecture's "Pregled predavanja" overview section, checking for missing
      terminal punctuation before the next heading) — zero abrupt endings
      found. No regeneration needed, no API cost incurred.
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
- [ ] **amd64 Docker build is untested.** Checked what it'd take: `buildx`
      isn't installed on this machine (only `docker-compose` is), so
      testing it means installing new tooling (`brew install docker-buildx`)
      and running a QEMU-emulated build of a 2.47GB image — both a real
      system change and likely a long-running one. Didn't do either without
      checking first, especially since Docker's own future here is already
      an open question (the item above). Worth revisiting once that's
      decided — no point verifying amd64 support for a packaging approach
      that might get dropped.

## P2 — larger, optional roadmap items

- [x] **Decide whether Docker packaging is worth continuing to maintain —
      decided: keep it.** This project's own framing is "built for a real
      university course" — the realistic use case is handing this to
      classmates who aren't developers and won't set up Python/ffmpeg/
      tesseract themselves. `docker compose up` with zero other setup is
      worth the reduced capability for that audience, as long as it's
      never presented as the *best* path (already fixed in P0 — README
      now recommends developer mode when Python is available, and
      Docker's limitations are stated up front rather than discovered
      mid-run). No code change from this decision beyond what P0 already
      did; the amd64-build-untested item above is the one open follow-up,
      deliberately left for when it's actually needed rather than done
      speculatively.

- [ ] **Opt-in cloud transcription backend** (Groq-hosted Whisper, OpenAI,
      Deepgram/AssemblyAI, ElevenLabs Scribe) for machines without a usable
      GPU — the seam already exists (`WHISPER_BACKEND` dispatch in
      `01_transcribe.py` returns a uniform `{language, segments}` shape).
      Must stay opt-in and clearly labeled — recordings leaving the machine
      is a real privacy trade-off for a project that's deliberately
      local-first. Require segment/word-level timestamps (segmentation
      depends on them) and keep the vocabulary-priming trick.
- [x] **Better OCR/matching, cheap first step done: image-hash pre-pass.**
      Added `frame_hash`/`hamming_distance` (dHash, PIL only, no new
      dependency) to stage 4: consecutive event frames within a small
      Hamming distance reuse the previous frame's OCR text instead of
      spending a second OCR call. Threshold (3 of 64 bits) picked from
      real data, not guessed: checked all 22 lectures' actual detected
      events first — several (lecture17, 21, 22, 10, 06, 14, 18, 20) have
      genuine near-duplicate consecutive events (Hamming distance 0-3,
      e.g. a cursor-triggered false slide-change or an animation frame),
      while the closest distance between two frames stage 3 judged
      genuinely *different* stayed well clear of that threshold across
      the whole course — zero false-merge risk at this setting on the
      data that exists. Verified end-to-end through the real module
      against real frame images (not just the standalone calibration
      script) plus 5 new synthetic-image unit tests.
      **Still open, bigger scope:** the actual formula-OCR-quality problem
      (Tesseract mangles math) isn't touched by this — that needs a cloud
      vision OCR or a multimodal "which slide is this" approach, both
      real design decisions (cost, privacy, new dependency) left for when
      OCR quality is actually the bottleneck someone hits.
- [x] **Prompt caching for stage 6 — done. Batch API — deliberately not
      done, turned out not to be the drop-in change the TODO assumed.**
      `SYSTEM_PROMPT` (identical across every slide of every lecture) now
      goes in as a `cache_control: {"type": "ephemeral"}` content block
      instead of a plain string; cache read/write token counts are
      captured in `usage` and surfaced in both the per-slide and
      per-lecture log lines. Safe to ship even though the prompt's real
      token count relative to the caching minimum wasn't verified — the
      API silently skips caching for under-minimum blocks rather than
      erroring. **Not live-tested against a real API call** (would spend
      real API credits; didn't do that without asking — happy to run a
      cheap one-slide smoke test if you want it verified before relying
      on it).
      Batch API turned out to conflict with something real, not just be
      extra work: it's async/polled, but the web UI's live per-slide
      progress bar (`webui/progress.py`'s `_RE_NOTES` regex) depends on
      stage 6 streaming `[i/N] slide ...` lines to stdout as each call
      finishes — switching to Batch would trade that away for the cost
      cut. Worth doing later as an explicit opt-in mode (e.g. `--batch`
      falls back to an indeterminate spinner like stages 2/5/7 already
      do), not as a silent default swap.
- [x] **Cross-lecture topic index — implemented and run for real.**
      `07_assemble.py --topic-index` (off by default): one extra Claude
      call over the whole assembled guide, inserted after the TOC before
      the per-lecture content. Explicitly opt-in and CLI-only (not wired
      into the web UI or automatic pipeline runs yet) — a real API cost
      over potentially hundreds of thousands of tokens for a full course.
      Ran it for real against this course's 22-lecture guide with
      explicit go-ahead: 179,367 input / 2,795 output tokens (real
      tokenization came in higher than the ~110K rough estimate — Serbian
      text + LaTeX is denser than plain English). Output quality is
      genuinely good on inspection: correct Serbian, working `#lectureNN`
      anchor links, real cross-lecture groupings (e.g. jitter/SNR tracked
      across 5 lectures), and a "Recurring exam-relevant emphases" section
      that actually found repeated material (the FPGA-clock-jitter warning
      appearing in 7 different lectures). `study_guide.md` and
      `study_guide.pdf` both regenerated with it included.

---
Sources: `DOCUMENTATION.md` §5 (pre-existing roadmap), repo inspection
2026-08-11 (no `.git`, no `.gitignore`, stale `.env.example`, divergent
`data/` vs top-level trees).
