"""Shared fixtures for the FastAPI TestClient suite (T1).

The tricky part: several modules bind webui.config's path constants into
their *own* module namespace at import time (`from .config import
OUTPUT_DIR` etc.), so monkeypatching `webui.config.OUTPUT_DIR` alone does
nothing for code in those modules -- each module's own copy of the name
has to be repointed too. A `grep -n "from .config import" webui/*.py`
turns up:

    webui/jobs.py:     LOGS_DIR, PROJECT_ROOT, SCRIPTS_DIR, VIDEOS_DIR
    webui/review.py:   OUTPUT_DIR
    webui/progress.py: INPUT_DIR, OUTPUT_DIR
    webui/main.py:     OUTPUT_DIR, PROJECT_ROOT

(webui/preflight.py imports the *function* `get_api_key`, not a path
constant -- that one's fine as-is, since the function body still looks up
`ENV_PATH` in config's own module globals every call.)

webui/decks.py, webui/media.py, webui/routes/* all do `from . import
config` / `from .. import config` and access `config.WHATEVER` through the
module reference, so those pick up monkeypatched config attributes for
free.

webui.main also runs `OUTPUT_DIR.mkdir(...)` and mounts `StaticFiles` at
`/files` pointed at `OUTPUT_DIR` **at import time** -- that mount is baked
into the app object and can't be repointed by later monkeypatching. So the
paths get redirected to a *session*-scoped tmp directory before `webui.main`
is ever imported (nothing in tests/ imports it before this fixture runs),
and a function-scoped autouse fixture wipes/rebuilds that same directory
tree before every test for per-test isolation. Tests must never assert on
`/files/...` static-mount content for a *newly swapped* directory --  that
part of the app is fixed at first import.
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
    """Repoint every module's path constants at `_project_root`, once, before
    `webui.main` (and therefore its StaticFiles mount) is ever imported."""
    from webui import config

    root = _project_root
    # config.PROJECT_ROOT is deliberately left pointing at the *real* repo
    # root: webui/main.py computes STATIC_DIR = PROJECT_ROOT / "webui" /
    # "static" at import time and mounts it immediately, so if PROJECT_ROOT
    # were repointed here, main.py would try (and fail) to serve the app's
    # own JS/CSS from a tmp dir that doesn't have a webui/static/ under it.
    # Nothing reads config.PROJECT_ROOT for user data -- every data path
    # below is repointed explicitly instead of being derived from it.
    config.SCRIPTS_DIR = root / "scripts"
    config.INPUT_DIR = root / "input"
    config.OUTPUT_DIR = root / "output"
    config.VIDEOS_DIR = config.INPUT_DIR / "videos"
    config.SLIDES_DIR = config.INPUT_DIR / "slides"
    config.LOGS_DIR = config.OUTPUT_DIR / "logs"
    config.ENV_PATH = root / ".env"
    config.VIDEO_URLS_PATH = config.INPUT_DIR / "video_urls.json"
    config.LECTURES_META_PATH = config.INPUT_DIR / "lectures.json"

    from webui import jobs, progress, review

    jobs.LOGS_DIR = config.LOGS_DIR
    # Unlike config.PROJECT_ROOT above, jobs.PROJECT_ROOT *is* repointed at
    # the tmp root: jobs.py uses it as the subprocess cwd, so leaving it at
    # the real repo root would make the scheduler run stage subprocesses
    # from the wrong cwd.
    jobs.PROJECT_ROOT = root
    jobs.SCRIPTS_DIR = config.SCRIPTS_DIR
    # get_video_duration() reads jobs.VIDEOS_DIR (re-exported from config,
    # same pattern as the others above) to build the videos/ path.
    jobs.VIDEOS_DIR = config.VIDEOS_DIR
    progress.INPUT_DIR = config.INPUT_DIR
    progress.OUTPUT_DIR = config.OUTPUT_DIR
    review.OUTPUT_DIR = config.OUTPUT_DIR

    from webui import main as main_module

    main_module.OUTPUT_DIR = config.OUTPUT_DIR
    # main_module.PROJECT_ROOT is deliberately left alone too, for the same
    # STATIC_DIR reason as config.PROJECT_ROOT above.

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
