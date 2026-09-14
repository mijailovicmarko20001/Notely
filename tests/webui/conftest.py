"""Shared fixtures for the FastAPI TestClient suite (T1).

Phase 6 (cleanup plan): webui/jobs.py, webui/progress.py, webui/review.py,
and webui/main.py used to bind webui.config's path constants into their
*own* module namespace at import time (`from .config import OUTPUT_DIR`
etc.), so monkeypatching `webui.config.OUTPUT_DIR` alone did nothing for
code in those modules -- each module's own copy of the name had to be
repointed too. They now do `from . import config` and read `config.X`
through the module reference instead (same pattern webui/decks.py,
webui/media.py, and webui/routes/* already used), so patching
webui.config's own attributes is enough for all of them.

webui.main also runs `config.OUTPUT_DIR.mkdir(...)` and mounts
`StaticFiles` at `/files` pointed at `config.OUTPUT_DIR` **at import
time** -- that mount is baked into the app object and can't be repointed
by later monkeypatching. So the paths get redirected to a *session*-scoped
tmp directory before `webui.main` is ever imported (nothing in tests/
imports it before this fixture runs), and a function-scoped autouse
fixture wipes/rebuilds that same directory tree before every test for
per-test isolation. Tests must never assert on `/files/...` static-mount
content for a *newly swapped* directory -- that part of the app is fixed
at first import.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mkdirs(root: Path) -> None:
    (root / "input" / "videos").mkdir(parents=True, exist_ok=True)
    (root / "input" / "slides").mkdir(parents=True, exist_ok=True)
    (root / "input" / "exams").mkdir(parents=True, exist_ok=True)
    (root / "output" / "logs").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(parents=True, exist_ok=True)


def _write_default_lectures(root: Path) -> None:
    (root / "input" / "video_urls.json").write_text(
        json.dumps(
            {
                "lecture01": "https://youtu.be/aaaaaaaaaaa",
                "lecture02": "https://youtu.be/bbbbbbbbbbb",
            }
        )
    )


@pytest.fixture(scope="session")
def _project_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("notely_webui_root")
    _mkdirs(root)
    _write_default_lectures(root)
    return root


@pytest.fixture(scope="session")
def _patched_paths(_project_root):
    """Repoint webui.config's path constants at `_project_root`, once,
    before `webui.main` (and therefore its StaticFiles mount) is ever
    imported. jobs.py/progress.py/review.py/main.py all read config.X
    through the module reference (Phase 6), so patching config's own
    attributes here is enough for all of them -- no per-module copies to
    repoint separately.

    config.PROJECT_ROOT *is* repointed at the tmp root (jobs.py uses it as
    the subprocess cwd for stage scripts). This is safe for
    webui/main.py's STATIC_DIR too, unlike before Phase 6: STATIC_DIR is
    now Path(__file__).resolve().parent / "static" (this file's own
    directory), not derived from PROJECT_ROOT at all."""
    from webui import config

    root = _project_root
    config.PROJECT_ROOT = root
    config.SCRIPTS_DIR = root / "scripts"
    config.INPUT_DIR = root / "input"
    config.OUTPUT_DIR = root / "output"
    config.VIDEOS_DIR = config.INPUT_DIR / "videos"
    config.SLIDES_DIR = config.INPUT_DIR / "slides"
    config.EXAMS_DIR = config.INPUT_DIR / "exams"
    config.OUTPUT_EXAMS_DIR = config.OUTPUT_DIR / "exams"
    config.OUTPUT_ESSENTIALS_DIR = config.OUTPUT_DIR / "essentials"
    config.COURSE_ESSENTIALS_PATH = config.OUTPUT_DIR / "essentials.md"
    config.LOGS_DIR = config.OUTPUT_DIR / "logs"
    config.ENV_PATH = root / ".env"
    config.VIDEO_URLS_PATH = config.INPUT_DIR / "video_urls.json"
    config.LECTURES_META_PATH = config.INPUT_DIR / "lectures.json"

    return config


@pytest.fixture(scope="session")
def app(_patched_paths):
    from webui.main import app as fastapi_app

    return fastapi_app


@pytest.fixture(autouse=True)
def _reset_project_root(_project_root, _patched_paths):
    """Wipe and rebuild the shared tmp project root before every test so
    upload/settings/job state from one test never leaks into the next --
    needed because the app's /files mount is bound to this exact directory
    (see module docstring), so we can't just hand each test a brand new
    tmp_path the way a normal fixture would."""
    root = _project_root
    if root.exists():
        shutil.rmtree(root)
    _mkdirs(root)
    _write_default_lectures(root)
    yield


@pytest.fixture(autouse=True)
def _reset_job_manager():
    """jobs.MANAGER is a process-wide singleton; give each test a fresh one
    so job state (busy flag, events, task list) can't leak across tests."""
    from webui import jobs

    jobs.MANAGER.__init__()
    yield
    jobs.MANAGER.__init__()


@pytest.fixture()
def client(app):
    return TestClient(app, base_url="http://localhost")


@pytest.fixture()
def project_root(_project_root, _patched_paths):
    """The active tmp project root, for tests that need to write fixture
    files (timelines, decks, .env) directly rather than through the API."""
    return _project_root
