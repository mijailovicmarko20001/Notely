# Contributing to Notely

This is a personal-study-tool-turned-open-source-project, maintained
solo — contributions are welcome, but keep expectations calibrated to
that: response times aren't guaranteed, and small, focused PRs are much
easier to review than large ones.

## Dev setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e ".[dev]"   # pytest, ruff, and the test-only httpx/anyio
```

For an exact reproducible install of the *runtime* dependencies (matches the
maintainer's validated dev environment) use `requirements-lock.txt` in place
of `requirements.txt` above — see its header comment. Still install `.[dev]`
separately; it isn't part of the lock file.

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

- Formatting and a correctness-only lint pass (unused imports/variables,
  undefined names, a handful of real-bug-shaped checks) are enforced by
  [ruff](https://docs.astral.sh/ruff/) — `ruff check .` and
  `ruff format --check .`, both run in CI and available as a pre-commit hook
  (`pre-commit install` once, or run `ruff check --fix . && ruff format .`
  by hand before committing). See `pyproject.toml` for the exact rule set —
  it's deliberately narrow, not a full style enforcer. Whole-codebase
  reformats are listed in `.git-blame-ignore-revs`; run
  `git config blame.ignoreRevsFile .git-blame-ignore-revs` once per checkout
  (or pass `--ignore-revs-file` per invocation) so `git blame` skips past
  them to real authorship.
- Beyond what ruff checks, match the surrounding code — comment density,
  naming, and structure vary a bit stage to stage; keep new code consistent
  with whatever file you're editing.
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
  `TODO.md`, reference it and check the item off (or delete it) in the
  same PR.

## Reporting bugs / requesting features

Open a GitHub issue. For anything security-relevant, see `SECURITY.md`
instead — please don't open a public issue for those.

## Reporting a vulnerability

See `SECURITY.md`.
