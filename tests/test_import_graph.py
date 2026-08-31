"""Import-graph guard: the plan's one load-bearing invariant is that
dependencies point inward only -- notely/ (the ports/adapters package
Phase 3 onward extracts) must never import from webui/ or scripts/. Stages
depend on ports (Protocols), never the other way around, and adapters are
injected at the entry point (scripts/webui), not imported by notely/ itself.

Written now, in Phase 1, before notely/ has a single file in it -- so the
guard is in place from notely/'s very first commit rather than bolted on
after the fact. Passes trivially today (skipped) since notely/ doesn't
exist yet; starts actually walking files the moment Phase 3/4 creates it."""

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NOTELY_DIR = PROJECT_ROOT / "notely"

FORBIDDEN_TOP_LEVEL_IMPORTS = {"webui", "scripts"}


def _imported_top_level_names(path: Path) -> set[str]:
    """Top-level package names this file imports via `import x[.y]` or
    `from x[.y] import z` (module.level == 0, i.e. not a relative import --
    relative imports can't reach outside notely/ at all)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_notely_package_never_imports_webui_or_scripts():
    if not NOTELY_DIR.is_dir():
        pytest.skip("notely/ doesn't exist yet -- see Phase 3/4 of the cleanup plan")

    offenders = {}
    for path in sorted(NOTELY_DIR.rglob("*.py")):
        bad = _imported_top_level_names(path) & FORBIDDEN_TOP_LEVEL_IMPORTS
        if bad:
            offenders[str(path.relative_to(PROJECT_ROOT))] = sorted(bad)

    assert offenders == {}, f"notely/ modules importing webui/scripts (dependency rule violated): {offenders}"
