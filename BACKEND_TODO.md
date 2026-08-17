# BACKEND_TODO.md — security & architecture tasks for the web UI backend

Grounded audit of `webui/` (2026-08-12). Each task is tagged with the subagent
that should do it (defined in `.claude/agents/`, all running Sonnet 5):

- `backend-security-hardener` — S-tasks
- `backend-concurrency-fixer` — C-tasks
- `backend-api-architect` — A-tasks
- `backend-test-writer` — T-tasks (runs last, locks in the others' fixes)

Suggested order: S → C → A → T. Within a group, tasks are priority-ordered.
Context that shapes every fix: this is a local single-user tool, but
`docker-compose.yml` publishes `8000:8000` (LAN-reachable), so "it's only
localhost" is not a safe assumption.

---

## P0 — Security (`backend-security-hardener`)

### S1. Path traversal via `lecture_ids` form field in slide upload
`webui/api.py:263-289` (`upload_slides`): the comma-separated `lecture_ids`
string is used verbatim in `SLIDES_DIR / f"{lecture_id}.{ext}"`. A value like
`../../evil` writes a PDF anywhere the process can write. Fix: validate each
id against the known lectures in `video_urls.json` (or at minimum
`^lecture\d{2,}$`), reject the request otherwise. Apply the same validation to
the `lecture_id` path parameter in `get_review`, `post_review` (`api.py:361-381`)
and `preview_frame` (`api.py:417`), and inside `review.py` — Starlette's path
matching blocks `/` in segments, but ids still reach the filesystem unvalidated.
Centralize this in one `validate_lecture_id()` helper.

### S2. No origin/host protection → CSRF + DNS-rebinding against the API
No auth, no `Origin` check, no trusted-host check anywhere (`webui/main.py`).
Any website open in the user's browser can fire cross-site requests at
`http://127.0.0.1:8000/api/*` (start jobs, overwrite slide decks, change
settings including the stored Anthropic key path), and DNS rebinding defeats
CORS entirely. Fix: add Starlette `TrustedHostMiddleware`
(`localhost`, `127.0.0.1`, configurable extra host for Docker) and reject
state-changing requests whose `Origin`/`Referer` is present but not our own.

### S3. Docker publishes the UI to the LAN with zero auth
`docker-compose.yml:5` (`"8000:8000"`) binds all interfaces. Fix: default the
mapping to `127.0.0.1:8000:8000`, and add an optional shared-token auth
(e.g. `NOTELY_AUTH_TOKEN` env → required `Authorization` header + login prompt
in the UI) for anyone who deliberately exposes it beyond localhost. Document
the trade-off in the compose file.

### S4. `.env` line injection through settings writes
`webui/config.py:63-71` (`write_settings`): values are written with
`quote_mode="never"`; a JSON body value containing a newline (e.g.
`"WHISPER_MODEL": "medium\nANTHROPIC_API_KEY=attacker"`) injects arbitrary
lines into `.env`, which `stage_env()` then feeds to every stage subprocess.
Fix: reject control characters/newlines in settings values (and length-cap
them) before `set_key`; consider `quote_mode="always"` for non-key settings.

### S5. Argument injection into yt-dlp via user-supplied URLs
`webui/playlist.py:14`: the raw URL is appended to the yt-dlp argv; a value
starting with `-` is parsed as a flag (yt-dlp has flags that write files and
run external commands). The same stored URLs later reach stage 0. Fix: require
the URL to parse as `http`/`https` with a hostname (reject everything else) in
`expand_playlist` **and** in `set_lectures` (`api.py:113-132`) before
persisting, and pass `--` before the URL in argv.

### S6. Raw exception strings returned to the client
`api.py:63` (`test_key`) and `api.py:110` return `str(e)` / tool stderr to the
browser; `guide_pdf` (`api.py:413`) returns the last 500 chars of stderr.
Low risk locally but leaks paths/environment details once LAN-exposed. Fix:
log full details server-side, return short generic messages + a log pointer.

---

## P1 — Concurrency & job-manager correctness (`backend-concurrency-fixer`)

### C1. `JobManager` mutates shared state under two different locks
`webui/jobs.py`: worker threads mutate `self.job["tasks"][i]` while holding
`_cond` (`_claim_next`, `_worker`) or **no lock at all**
(`_run_task`, e.g. `jobs.py:211-213` percent updates), while `snapshot()`
serializes `self.job` under `_lock` (`jobs.py:82-84`). JSON-serializing a dict
another thread is mutating can raise or produce torn snapshots. Fix: one lock
(or make `_cond` wrap `_lock` via `threading.Condition(self._lock)`) guarding
every read/write of `self.job`, `self.events`, `self._seq`.

### C2. `start_job` TOCTOU — two concurrent POSTs can both start
`api.py:295-312` checks `MANAGER.busy` then calls `start_job`, which re-checks
`busy` without a lock (`jobs.py:86-90`); two simultaneous requests can both
pass, the second silently resetting `self.job`/`events` under the first. Same
race in `post_review` (`api.py:369-381`). Fix: make claim-and-start atomic
inside `JobManager` (single lock; raise a typed `Busy` error), map it to 409
in the API layer, and drop the pre-check.

### C3. Event sequence resets between jobs break SSE resume
`start_job` resets `self.events = []` and `self._seq = 0` (`jobs.py:92-93`);
an SSE client reconnecting with `Last-Event-ID` from a previous job silently
skips or replays events of the new job (`api.py:328-356`). Fix: never reset
`_seq` (monotonic across jobs), or scope the SSE stream by job id and have the
client resubscribe per job.

### C4. `guide_pdf` regeneration race
`api.py:394-414`: two concurrent requests both see a stale PDF and spawn two
`08_export_pdf.py` processes writing the same output file. Fix: guard with a
module-level lock, and have the export write to a temp file + atomic rename.

### C5. Cancel doesn't reliably stop the final assembly task
`jobs.py:276-280`: `_run` checks `_cancelled` before each final task, but a
cancel arriving *during* stage 7 only terminates it because `cancel()` kills
procs in `_procs` — verify the `"final"` lane proc is registered there before
termination, and add a test for cancel-during-assembly.

---

## P2 — Architecture & validation (`backend-api-architect`)

### A1. Replace `body: dict` with Pydantic request models
`put_settings`, `expand`, `set_lectures`, `start_job`, `post_review` all take
untyped dicts and index into them (`e["url"]` at `api.py:122` → 500 on missing
key; `corr["timestamp"]` at `review.py:85` → 500). Define models
(`SettingsUpdate`, `LectureEntries`, `JobRequest`, `Corrections`) with field
validation (stage range, timestamp ≥ 0, slide_number int|None) so malformed
input yields 422s, and the OpenAPI docs become accurate.

### A2. Extract business logic out of route handlers
`upload_pool` (`api.py:135-207`) embeds PDF merge/dedup logic;
`suggest_slides` (`api.py:210-259`) embeds matching heuristics; `guide_pdf`
and `preview_frame` embed subprocess orchestration. Move these into
`webui/decks.py` / `webui/media.py` service modules so `api.py` only does
HTTP concerns. Keep the lazy `fitz`/`pptx` imports at service-module level.

### A3. Bound upload memory and size
`await f.read()` loads whole decks into RAM (`api.py:160`, `api.py:222`,
`api.py:273`). Stream to disk in chunks with a configurable max size
(reject oversized uploads with 413) — matters in the memory-constrained
Docker/colima setup.

### A4. Consistent error shape + logging
Introduce a small exception→HTTP mapping (typed domain errors from services →
4xx/5xx with `{"error": ...}`), and use `logging` instead of bare `pass`
(`api.py:240-241`) so unreadable decks are at least diagnosable. Complements
S6 (no raw stderr/exception text to clients).

### A5. Split `api.py` by resource
460 lines mixing settings, lectures, slides, jobs, review, guide. Split into
routers (`routes/settings.py`, `routes/slides.py`, `routes/jobs.py`,
`routes/review.py`) mounted under the same `/api` prefix. Mechanical, do last
so it doesn't conflict with S/C diffs.

---

## P3 — Tests (`backend-test-writer`)

### T1. API test suite with FastAPI TestClient
No tests exist for `webui/`. Cover: lecture-id validation rejects traversal
(S1), settings write rejects newline values (S4), playlist rejects non-http
URLs (S5), job start returns 409 when busy (C2), review corrections merge
logic (`review.apply_corrections` — pure function, easy wins), and
`progress.parse_line` per-stage parsing. Use `tmp_path`-based project-root
fixture (monkeypatch `webui.config` paths) so tests never touch real
`input/`/`output/`.

### T2. JobManager scheduling tests with a fake stage script
Drive `JobManager` with stub scripts (instant-exit Python files writing their
artifact) to pin down: lane parallelism, dependency ordering per lecture,
failure-skips-downstream, cancel semantics (C5), snapshot consistency under
concurrent polling (C1 regression).
