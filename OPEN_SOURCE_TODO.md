# OPEN_SOURCE_TODO.md — what's left before making this repo public

Grounded audit done 2026-08-17, ahead of open-sourcing. Good news up front:
`git log --all -p` and `git ls-files` turned up **no real secrets, no
course PII, no committed video/slide binaries** — `.gitignore` was already
doing its job (`.env`, `input/videos/`, `input/slides/`, `output/`,
`input/video_urls.json`/`lectures.json` are all excluded; the repo is 1.5MB).
`.env.example` and the `sk-ant-…` strings in `app.js`/`index.html` are
placeholders, not leaked keys — confirmed by reading them, not just
grepping. `README.md`, `.env.example`, and a passing 160-test suite already
exist. The remaining work is genuinely about *going public*, not cleanup of
things left lying around.

---

## P0 — legal blockers (resolve before the repo is visible to anyone)

### O1. No `LICENSE` file — DONE (2026-08-17)
Added `LICENSE` (MIT, per your choice) and a `## License` section to
`README.md` clarifying it covers the code only, not lecture videos/slides/
generated notes (those stay covered by the existing ToS/copyright caveat
already in the README).

### O2. `PyMuPDF` (`fitz`) is AGPL-3.0, and it's a load-bearing dependency — DONE (2026-08-17)
Swapped to `pypdfium2` (text extraction + page rendering — `scripts/02_extract_slides.py`,
`scripts/01_transcribe.py`'s vocab-priming, `webui/decks.py::_extract_preview_text`)
and `pypdf` (page-copying — `webui/decks.py::merge_pool`), both permissively
licensed (Apache-2.0/BSD-3, BSD-3 respectively). `PyMuPDF` uninstalled from
`.venv` and removed from `requirements.txt`/`requirements-lock.txt`;
`import fitz` no longer appears anywhere in `scripts/`/`webui/`. Added 9 new
tests (`tests/test_extract_slides.py`, `tests/webui/test_decks_pdf.py`) via
a hand-rolled minimal-PDF fixture (`tests/pdf_fixtures.py`) — this code path
had zero coverage before, under either library. Also spot-checked against
a real course PDF (`input/slides/lecture04.pdf`, gitignored, not committed):
title/body text and rendered PNGs came out correct, and pypdfium2's text
extraction turned out *more* accurate than pypdf's on the same file (pypdf
inserts spurious spaces from kerning on this document — harmless where it's
only used as a dedup hash key in `merge_pool`, but confirms pypdfium2 was
the right pick everywhere text is user-visible).
Full test suite: `.venv/bin/python -m pytest tests/ -q` → 169 passed.
Note this never touched Docker's `ffmpeg`/`tesseract-ocr` (GPL/Apache,
installed via `apt` in the `Dockerfile`) — those are invoked as external
subprocesses, not linked into your code, so ordinary "mere aggregation"
applies and there was nothing to resolve there.

### O3. Confirm no course copyright liability in what actually gets pushed — DONE (2026-08-17)
`git log --all --name-only` over every commit on every branch, filtered for
`.pptx`/`.pdf`/video/audio extensions and `input/videos/`, `input/slides/`,
`output/`, `data/` paths — zero matches. No course video, slide, or
generated-note file has ever been committed, at any point in this repo's
history. `.gitignore` and `CLAUDE.md`'s non-redistribution stance are doing
their job.

---

## P1 — repo hygiene expected of a public OSS project

### O4. No GitHub remote configured yet
`git remote -v` is empty — this has never been pushed anywhere. Decide the
repo's public name (worth a quick gut-check that "Notely" doesn't collide
with an existing product/trademark you'd rather not share a name with),
create the GitHub repo, and push. Do this *after* O1/O2/O5, not before —
a license and a clean CI run should exist before the first outside visitor
can see the repo, not get bolted on after.

### O5. No CI — DONE (2026-08-17)
Added `.github/workflows/tests.yml`: `pytest tests/ -q` on push to `main`
and on every PR, matrix over Python 3.11/3.12, `ubuntu-latest`. No system
packages installed — confirmed the suite never shells out to real
ffmpeg/tesseract/yt-dlp binaries (stub scripts / stop-before-subprocess
patterns throughout `tests/`), just `pip install -r requirements.txt`. Not
taken on faith: ran both matrix legs for real in matching
`python:3.11-slim`/`python:3.12-slim` containers before trusting the
workflow file — 169 passed on both.

### O6. amd64 Docker build is still unverified
Already flagged as open in `TODO.md` P0 ("amd64 Docker build is untested" —
`buildx` isn't installed, testing means a QEMU-emulated build of a 2.47GB
image). That was an acceptable thing to defer for a single-user personal
project; it's not acceptable to defer once strangers on Windows/Linux are
the primary audience for `docker compose up` (README's own "Quick start,
no Python setup" pitch). Either verify the amd64 build for real, or say so
explicitly in the README until it's done.

### O7. No contribution/security process docs — DONE (2026-08-17)
Added `CONTRIBUTING.md` (dev setup, `pytest tests/ -q`, service-module/
lazy-import/commit-message conventions) and `SECURITY.md` (points at
GitHub's private vulnerability reporting rather than a public email/issue;
explicitly calls out the LAN-exposed-Docker risk from S3/`NOTELY_AUTH_TOKEN`
as the realistic threat model, not generic boilerplate). `CODE_OF_CONDUCT.md`
still skipped deliberately — optional for a solo-maintained tool, add later
if the project grows contributors.

### O8. Resolve or drop the working-tree noise before the first public commit — DONE (2026-08-17)
`git status` is clean. The `06_generate_notes.py` prompt diff was a real,
complete change (not WIP) — committed on its own. `.claude/agents/*.md`
committed too (useful, non-sensitive context for contributors who also use
Claude Code; `.claude/settings.local.json` stays gitignored, personal/
machine-local). `BACKEND_TODO.md` committed, matching the existing
`TODO.md`/`FRONTEND_TODO.md` convention.

---

## P2 — content/config sanity for a stranger cloning this cold

### O9. `DOCUMENTATION.md` names a real institution
`DOCUMENTATION.md:19` and several other lines identify "University of
Belgrade" as the validating course, and describe specifics (Serbian-language
transcripts, hand-drawn-ink Zoom slides) tied to that real course. This
isn't a secret and isn't personally identifying beyond a public university
name, but it is a call worth making deliberately rather than by default:
keep it as a concrete case study (it's good, specific evidence the tool
works), or genericize it if you'd rather the README/docs not point at a
specific school. Your call, not a blocker.

### O10. `README.md`/`DOCUMENTATION.md` should link the license once O1 lands — DONE (2026-08-17)
Added as part of O1's commit — `README.md` now has a `## License` section.
No badge added (CI badge/license badge are cosmetic, skipped as low-value
relative to everything else on this list — easy to add later if wanted).

---

## Suggested order

~~O2~~ (done) → **O1** (pick + add `LICENSE`) → O3 (manual double-check) →
O8 (clean working tree) → O5 (CI) → O6 (verify amd64) → O7
(CONTRIBUTING/SECURITY) → O4 (push) → O9/O10 (polish).
