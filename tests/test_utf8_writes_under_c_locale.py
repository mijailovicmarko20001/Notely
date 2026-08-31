"""D5 (cleanup plan, Phase 2): two writes used the platform's default text
encoding (`open(path, "w")` with no `encoding=` argument) instead of an
explicit "utf-8" -- scripts/05_segment_transcript.py's save_json and
scripts/07_assemble.py's study-guide write. Under a non-UTF-8 locale (a
bare `LC_ALL=C`/`LANG=C` environment -- not exotic, it's what a minimal
Docker base image or CI runner often defaults to without an explicit
locale set), Python's default text encoding falls back to ASCII, so
writing this course's actual Serbian-language content (č/ć/š/ž/đ) raises
UnicodeEncodeError and the stage crashes.

Reproduced via a real subprocess with LC_ALL=C LANG=C PYTHONUTF8=0
PYTHONCOERCECLOCALE=0 -- monkeypatching locale.getpreferredencoding alone
doesn't affect open()'s encoding resolution (verified by hand), and macOS's
own default locale is already UTF-8, so only an actual subprocess
environment change reliably reproduces the bug.

Getting each test to actually reach its target write under this
environment surfaced two more locale-dependent open() calls in the same
two files that crash first, before the write is ever reached: 07's
load_json/read_lecture_notes (a raw UTF-8 note file can't even be *read*
under ascii) and 05's load_json. Fixed alongside the two originally-cited
writes rather than leaving the test unable to reach them -- same defect
class, same two files, same commit."""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_C_LOCALE_ENV = {
    **os.environ,
    "LC_ALL": "C",
    "LANG": "C",
    "PYTHONUTF8": "0",
    "PYTHONCOERCECLOCALE": "0",
}


def _run_under_c_locale(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=_C_LOCALE_ENV,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_stage05_save_json_writes_utf8_under_c_locale():
    code = """
import importlib.util, tempfile, pathlib
spec = importlib.util.spec_from_file_location("s05", "scripts/05_segment_transcript.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
path = pathlib.Path(tempfile.mktemp())
mod.save_json(path, {"transcript_text": "Ovo je \\u010d\\u0161\\u017e\\u0111\\u0107 test"})
raw = path.read_bytes()
assert "čšžđć".encode() in raw, raw  # ensure_ascii=False: real UTF-8 bytes, not \\u escapes
print("OK")
"""
    result = _run_under_c_locale(code)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "UnicodeEncodeError" not in result.stderr
    assert "OK" in result.stdout


def test_stage07_assemble_guide_writes_utf8_under_c_locale():
    code = """
import tempfile, pathlib
from notely.pipeline import assemble as mod
tmp = pathlib.Path(tempfile.mkdtemp())
mod.get_project_root = lambda: tmp
notes_dir = tmp / "output" / "notes"
notes_dir.mkdir(parents=True)
(notes_dir / "lecture01.md").write_text("# lecture01\\n\\nOvo je \\u010d\\u0161\\u017e\\u0111\\u0107 test\\n", encoding="utf-8")
ok = mod.assemble_guide(force=True, topic_index=False)
assert ok is True
raw = (tmp / "output" / "study_guide.md").read_bytes()
assert "čšžđć".encode() in raw, raw
print("OK")
"""
    result = _run_under_c_locale(code)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "UnicodeEncodeError" not in result.stderr
    assert "OK" in result.stdout
