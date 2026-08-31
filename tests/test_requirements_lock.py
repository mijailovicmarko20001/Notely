"""D8 (cleanup plan, Phase 2): requirements-lock.txt pinned opencv-python
(the full, GUI-linked build) where requirements.txt deliberately declares
opencv-python-headless -- see requirements.txt's own comment: "headless: no
GUI libs needed (Docker-friendly); cv2 API-identical". These are two
different PyPI packages, not two versions of the same one, so installing
from the lock file (which CONTRIBUTING.md tells users to do for a
reproducible install) silently pulls in the wrong one.

This test is a general name-substitution guard, not an opencv-specific
check: every package requirements.txt declares must appear in
requirements-lock.txt under the *same* normalized name (PyPI treats `-`,
`_`, and `.` as equivalent, and names case-insensitively). The lock file is
allowed to contain extra entries requirements.txt doesn't mention
(transitive dependencies) -- that's normal and not what this guards
against."""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _normalize(name: str) -> str:
    """PyPI package-name normalization (PEP 503): case-insensitive, and
    runs of -, _, . are all equivalent."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement_names(path: Path) -> set[str]:
    names = set()
    for raw_line in path.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()  # drop comments
        if not line:
            continue
        line = line.split(";", 1)[0].strip()  # drop environment markers
        line = re.sub(r"\[[^\]]*\]", "", line)  # drop extras, e.g. uvicorn[standard]
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if m:
            names.add(_normalize(m.group(1)))
    return names


def test_every_requirements_txt_package_appears_in_the_lock_file_by_the_same_name():
    req_names = _parse_requirement_names(PROJECT_ROOT / "requirements.txt")
    lock_names = _parse_requirement_names(PROJECT_ROOT / "requirements-lock.txt")

    missing = req_names - lock_names
    assert not missing, (
        f"requirements.txt package(s) not present under the same name in "
        f"requirements-lock.txt (name substitution, not just a stale version): {sorted(missing)}"
    )
