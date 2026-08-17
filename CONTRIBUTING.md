# Contributing to Notely

This is a personal-study-tool-turned-open-source-project, maintained
solo — contributions are welcome, but keep expectations calibrated to
that: response times aren't guaranteed, and small, focused PRs are much
easier to review than large ones.

## Dev setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

For an exact reproducible install (matches the maintainer's validated dev
environment) use `requirements-lock.txt` instead — see its header comment.

System binaries the pipeline needs at runtime (not required just to run the
test suite — see below): `ffmpeg`, `tesseract` (+ your lecture language's
trained data, e.g. `tesseract-ocr-eng`), and `libreoffice` if you'll be
extracting `.pptx` decks. `Dockerfile` installs all of these via `apt`, and
is the fastest way to see the whole pipeline running without setting them
up natively.

## Running tests

```bash
.venv/bin/python -m pytest tests/ -q
```

The suite is pure-Python logic against synthetic fixtures and stub scripts
— it never shells out to real `ffmpeg`/`tesseract`/`yt-dlp`, so it runs
the same whether or not those binaries are installed. CI
(`.github/workflows/tests.yml`) runs it on every push/PR against Python
3.11 and 3.12 on Linux; run it locally before opening a PR.

## Code style / conventions

- Match the surrounding code, not a style guide — comment density, naming,
  and structure vary a bit stage to stage; keep new code consistent with
  whatever file you're editing.
- Service logic belongs in `webui/*.py` modules (`decks.py`, `media.py`,
  `jobs.py`, `review.py`), not in `webui/routes/*.py` — routes should stay
  thin HTTP glue. See `webui/errors.py` for the typed-exception pattern
  used to get consistent HTTP status codes without a try/except per route.
- Lazy-import heavy libraries (`pypdfium2`, `pypdf`, `pptx`, `anthropic`,
  `cv2`) inside the function that needs them, not at module top level —
  keeps server startup fast.
- Commit messages: one topic per commit, explain *why* not just *what* (see
  `git log` for the convention — e.g. "Concurrency: unify JobManager
  locking, fix start/cancel races"). If a change is driven by an item in
  one of the `*_TODO.md` files, reference it (e.g. "see OPEN_SOURCE_TODO.md
  O2") and update that file's checkbox/notes in the same PR.

## Reporting bugs / requesting features

Open a GitHub issue. For anything security-relevant, see `SECURITY.md`
instead — please don't open a public issue for those.

## Reporting a vulnerability

See `SECURITY.md`.
