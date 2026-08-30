"""Environment checks surfaced on the UI's setup screen.

Missing tesseract is the nastiest failure mode: stage 04 catches OCR errors
per-frame and degrades to empty text, silently producing garbage timelines —
so we check up front instead.
"""

import shutil
import subprocess
import sys

from .config import get_api_key


def _run_ok(cmd: list, timeout: int = 15) -> tuple:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return False, "not found"
    except subprocess.TimeoutExpired:
        return False, "timed out"


def check_binary(name: str) -> dict:
    path = shutil.which(name)
    return {"ok": path is not None, "detail": path or "not on PATH"}


def check_tesseract_lang(lang_spec: str) -> dict:
    """Verify every language in a spec like 'srp_latn+eng' is installed."""
    ok, out = _run_ok(["tesseract", "--list-langs"])
    if not ok:
        return {"ok": False, "detail": "tesseract not runnable"}
    installed = {line.strip() for line in out.splitlines()}
    missing = [lang for lang in lang_spec.split("+") if lang and lang not in installed]
    return {
        "ok": not missing,
        "detail": "all languages installed" if not missing else f"missing traineddata: {', '.join(missing)}",
    }


def check_soffice() -> dict:
    for name in ("soffice", "libreoffice"):
        if shutil.which(name):
            return {"ok": True, "detail": shutil.which(name)}
    return {"ok": False, "detail": "LibreOffice not found — .pptx decks unsupported, use PDF"}


def check_yt_dlp() -> dict:
    ok, out = _run_ok([sys.executable, "-m", "yt_dlp", "--version"])
    return {"ok": ok, "detail": out.splitlines()[0] if ok and out else out}


def check_whisper_model(model_size: str) -> dict:
    """Is the faster-whisper model already in the HF cache? (informational)"""
    try:
        from huggingface_hub import scan_cache_dir

        repo_name = f"Systran/faster-whisper-{model_size}"
        for repo in scan_cache_dir().repos:
            if repo.repo_id == repo_name and repo.size_on_disk > 100_000_000:
                return {"ok": True, "detail": f"{repo_name} cached ({repo.size_on_disk // 1_000_000} MB)"}
        return {"ok": False, "detail": f"{repo_name} not cached — first transcription downloads it (~1.5 GB for medium)"}
    except Exception as e:  # cache scan is best-effort, never fatal
        return {"ok": False, "detail": f"could not scan HF cache: {e}"}


def run_preflight(ocr_lang: str, whisper_model: str) -> dict:
    checks = {
        "ffmpeg": check_binary("ffmpeg"),
        "ffprobe": check_binary("ffprobe"),
        "tesseract": check_binary("tesseract"),
        "tesseract_langs": check_tesseract_lang(ocr_lang),
        "libreoffice": check_soffice(),
        "yt_dlp": check_yt_dlp(),
        "js_runtime": {
            "ok": shutil.which("deno") is not None or shutil.which("node") is not None,
            "detail": shutil.which("deno") or shutil.which("node")
            or "no deno/node — YouTube downloads may miss formats (yt-dlp deprecation)",
        },
        "whisper_model": check_whisper_model(whisper_model),
        "api_key": {
            "ok": bool(get_api_key()),
            "detail": "set" if get_api_key() else "not set — note generation (stage 6) locked",
        },
    }
    # soffice, whisper cache, and api key are warnings, not blockers
    required = ("ffmpeg", "ffprobe", "tesseract", "tesseract_langs", "yt_dlp")
    checks_ok = all(checks[k]["ok"] for k in required)
    return {"ok": checks_ok, "checks": checks}
