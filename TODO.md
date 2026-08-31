# Notely — open items

This file tracks genuinely open work only. Everything that's been
completed — the original P0-P3 pipeline hardening pass, the backend
security/concurrency/architecture audit (`BACKEND_TODO.md`, now deleted),
three rounds of frontend design work (`FRONTEND_TODO.md`, now deleted),
and the open-sourcing checklist (`OPEN_SOURCE_TODO.md`, now deleted) — is
summarized in `DOCUMENTATION.md` (§2.2 for the backend/frontend passes,
§3 for the pipeline decision log) and, for full task-by-task detail, in
the git log itself (`git log --oneline` — commit messages document the
verification done for each item, not just the change).

## Open

- [x] **amd64 Docker build was unverified — now checked on every push/PR.**
      `.github/workflows/tests.yml`'s `docker-build` job builds the `base`
      target on `ubuntu-latest` (amd64-native runners, so no local
      `buildx`/QEMU setup needed at all). Build-only, not pushed to a
      registry. The `gpu` target (NVIDIA CUDA libs) isn't built in CI —
      it needs a GPU to be meaningfully tested, not just to compile — so
      that stays manually verified on real hardware if it's ever changed.
- [ ] **The Study Guide's Table of Contents shows only `lectureNN`, no
      titles.** `scripts/07_assemble.py`'s `build_table_of_contents`
      (now `notely/pipeline/assemble.py`) builds the TOC from bare
      lecture ids — the titles are already stored in
      `input/lectures.json`, just not threaded through. A small backend
      addition, not a frontend one (confirmed against the real guide:
      `lecture01` through `lecture22`, no lecture content visible in the
      list at all).
