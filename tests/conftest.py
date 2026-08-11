"""Shared test setup.

Puts the project root on sys.path so `import webui...` works regardless of
how pytest is invoked, and provides a helper for importing the numbered
scripts/NN_name.py stage scripts, whose filenames (leading digit) aren't
valid Python module names for a plain `import`.
"""

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_stage(filename: str):
    """Import scripts/<filename> as a fresh module and return it."""
    path = SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
