"""Characterization tests for webui/preflight.py (previously zero coverage):
the setup-screen environment checks. Every external call (subprocess,
shutil.which, huggingface_hub) is faked so these run with none of the real
binaries installed."""

import subprocess

from webui import preflight


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --- check_binary ------------------------------------------------------------


def test_check_binary_found(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: f"/usr/bin/{name}")
    result = preflight.check_binary("ffmpeg")
    assert result == {"ok": True, "detail": "/usr/bin/ffmpeg"}


def test_check_binary_not_found(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    result = preflight.check_binary("ffmpeg")
    assert result == {"ok": False, "detail": "not on PATH"}


# --- check_tesseract_lang -----------------------------------------------------


def test_check_tesseract_lang_all_installed(monkeypatch):
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(0, stdout="eng\nsrp_latn\nosd\n"),
    )
    result = preflight.check_tesseract_lang("srp_latn+eng")
    assert result == {"ok": True, "detail": "all languages installed"}


def test_check_tesseract_lang_reports_missing(monkeypatch):
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(0, stdout="eng\nosd\n"),
    )
    result = preflight.check_tesseract_lang("srp_latn+eng")
    assert result["ok"] is False
    assert "srp_latn" in result["detail"]
    assert "eng" not in result["detail"].split("missing traineddata: ")[1]


def test_check_tesseract_lang_not_runnable(monkeypatch):
    def _raise(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(preflight.subprocess, "run", _raise)
    result = preflight.check_tesseract_lang("eng")
    assert result == {"ok": False, "detail": "tesseract not runnable"}


def test_check_tesseract_lang_timeout_treated_as_not_runnable(monkeypatch):
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="tesseract", timeout=15)

    monkeypatch.setattr(preflight.subprocess, "run", _raise)
    result = preflight.check_tesseract_lang("eng")
    assert result == {"ok": False, "detail": "tesseract not runnable"}


# --- check_soffice -------------------------------------------------------------


def test_check_soffice_found_as_soffice(monkeypatch):
    # check_soffice delegates to LibreOfficeDocConverter.find_binary (see
    # notely/adapters/libreoffice_doc_converter.py; its own contract test
    # covers find_binary's PATH-vs-app-bundle logic in detail) -- patch
    # that seam directly rather than shutil, which it no longer touches.
    monkeypatch.setattr(preflight.LibreOfficeDocConverter, "find_binary", lambda self: "/usr/bin/soffice")
    result = preflight.check_soffice()
    assert result == {"ok": True, "detail": "/usr/bin/soffice"}


def test_check_soffice_found_as_libreoffice_fallback(monkeypatch):
    monkeypatch.setattr(preflight.LibreOfficeDocConverter, "find_binary", lambda self: "/usr/bin/libreoffice")
    result = preflight.check_soffice()
    assert result == {"ok": True, "detail": "/usr/bin/libreoffice"}


def test_check_soffice_not_found(monkeypatch):
    monkeypatch.setattr(preflight.LibreOfficeDocConverter, "find_binary", lambda self: None)
    result = preflight.check_soffice()
    assert result["ok"] is False
    assert "not found" in result["detail"]


# --- check_yt_dlp --------------------------------------------------------------


def test_check_yt_dlp_ok(monkeypatch):
    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: _FakeCompleted(0, stdout="2024.12.06\n"))
    result = preflight.check_yt_dlp()
    assert result == {"ok": True, "detail": "2024.12.06"}


def test_check_yt_dlp_not_ok(monkeypatch):
    monkeypatch.setattr(
        preflight.subprocess, "run", lambda *a, **k: _FakeCompleted(1, stderr="module not found")
    )
    result = preflight.check_yt_dlp()
    assert result["ok"] is False


# --- check_whisper_model -------------------------------------------------------


def test_check_whisper_model_cached(monkeypatch):
    class _Repo:
        repo_id = "Systran/faster-whisper-medium"
        size_on_disk = 500_000_000

    class _Cache:
        repos = [_Repo()]

    monkeypatch.setattr("huggingface_hub.scan_cache_dir", lambda: _Cache())
    result = preflight.check_whisper_model("medium")
    assert result["ok"] is True
    assert "cached" in result["detail"]


def test_check_whisper_model_not_cached(monkeypatch):
    class _Cache:
        repos = []

    monkeypatch.setattr("huggingface_hub.scan_cache_dir", lambda: _Cache())
    result = preflight.check_whisper_model("medium")
    assert result["ok"] is False
    assert "not cached" in result["detail"]


def test_check_whisper_model_scan_failure_is_non_fatal(monkeypatch):
    def _raise():
        raise OSError("cache dir unreadable")

    monkeypatch.setattr("huggingface_hub.scan_cache_dir", _raise)
    result = preflight.check_whisper_model("medium")
    assert result["ok"] is False
    assert "could not scan HF cache" in result["detail"]


# --- run_preflight (aggregation) ------------------------------------------------


def test_run_preflight_ok_when_only_required_checks_pass(monkeypatch):
    # soffice, whisper cache, and API key are warnings, not blockers -- ok
    # must stay True even though all three fail here.
    monkeypatch.setattr(preflight, "check_binary", lambda name: {"ok": True, "detail": name})
    monkeypatch.setattr(preflight, "check_tesseract_lang", lambda spec: {"ok": True, "detail": "ok"})
    monkeypatch.setattr(preflight, "check_soffice", lambda: {"ok": False, "detail": "not found"})
    monkeypatch.setattr(preflight, "check_yt_dlp", lambda: {"ok": True, "detail": "1.0"})
    monkeypatch.setattr(preflight, "check_whisper_model", lambda model: {"ok": False, "detail": "not cached"})
    monkeypatch.setattr(preflight, "get_api_key", lambda: "")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    result = preflight.run_preflight("eng", "medium")
    assert result["ok"] is True
    assert result["checks"]["libreoffice"]["ok"] is False
    assert result["checks"]["api_key"]["ok"] is False


def test_run_preflight_not_ok_when_a_required_check_fails(monkeypatch):
    monkeypatch.setattr(preflight, "check_binary", lambda name: {"ok": name != "ffmpeg", "detail": name})
    monkeypatch.setattr(preflight, "check_tesseract_lang", lambda spec: {"ok": True, "detail": "ok"})
    monkeypatch.setattr(preflight, "check_soffice", lambda: {"ok": True, "detail": "found"})
    monkeypatch.setattr(preflight, "check_yt_dlp", lambda: {"ok": True, "detail": "1.0"})
    monkeypatch.setattr(preflight, "check_whisper_model", lambda model: {"ok": True, "detail": "cached"})
    monkeypatch.setattr(preflight, "get_api_key", lambda: "sk-test")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/node")

    result = preflight.run_preflight("eng", "medium")
    assert result["ok"] is False
    assert result["checks"]["ffmpeg"]["ok"] is False


# --- check_cloud_transcription_key (Feature C) ---------------------------------


def test_check_cloud_transcription_key_groq_set(monkeypatch):
    monkeypatch.setattr(preflight, "get_secret", lambda key: "gsk_test" if key == "GROQ_API_KEY" else "")
    result = preflight.check_cloud_transcription_key("groq")
    assert result == {"ok": True, "detail": "set"}


def test_check_cloud_transcription_key_openai_unset(monkeypatch):
    monkeypatch.setattr(preflight, "get_secret", lambda key: "")
    result = preflight.check_cloud_transcription_key("openai")
    assert result["ok"] is False
    assert "OPENAI_API_KEY" in result["detail"]


def test_check_cloud_transcription_key_reads_the_right_env_var_per_backend(monkeypatch):
    seen = []
    monkeypatch.setattr(preflight, "get_secret", lambda key: seen.append(key) or "")
    preflight.check_cloud_transcription_key("groq")
    preflight.check_cloud_transcription_key("openai")
    assert seen == ["GROQ_API_KEY", "OPENAI_API_KEY"]


# --- run_preflight is backend-aware (Feature C) --------------------------------


def _stub_common_checks(monkeypatch, extra_ok=True):
    monkeypatch.setattr(preflight, "check_binary", lambda name: {"ok": True, "detail": name})
    monkeypatch.setattr(preflight, "check_tesseract_lang", lambda spec: {"ok": True, "detail": "ok"})
    monkeypatch.setattr(preflight, "check_soffice", lambda: {"ok": True, "detail": "found"})
    monkeypatch.setattr(preflight, "check_yt_dlp", lambda: {"ok": True, "detail": "1.0"})
    monkeypatch.setattr(preflight, "get_api_key", lambda: "sk-test")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/node")


def test_run_preflight_checks_whisper_model_cache_for_local_backends(monkeypatch):
    _stub_common_checks(monkeypatch)
    monkeypatch.setattr(preflight, "check_whisper_model", lambda model: {"ok": True, "detail": "cached"})

    result = preflight.run_preflight("eng", "medium", whisper_backend="faster-whisper")

    assert "whisper_model" in result["checks"]
    assert "cloud_transcription_key" not in result["checks"]


def test_run_preflight_checks_cloud_key_instead_of_whisper_cache_for_cloud_backends(monkeypatch):
    _stub_common_checks(monkeypatch)
    monkeypatch.setattr(
        preflight, "check_whisper_model", lambda model: {"ok": False, "detail": "should not run"}
    )
    monkeypatch.setattr(
        preflight, "check_cloud_transcription_key", lambda backend: {"ok": True, "detail": "set"}
    )

    result = preflight.run_preflight("eng", "medium", whisper_backend="groq")

    assert "cloud_transcription_key" in result["checks"]
    assert "whisper_model" not in result["checks"]


def test_run_preflight_missing_cloud_key_is_a_warning_not_a_blocker(monkeypatch):
    # same philosophy as the existing api_key/soffice/whisper_model checks:
    # "you haven't configured X yet" warns, it doesn't fail preflight outright
    _stub_common_checks(monkeypatch)
    monkeypatch.setattr(
        preflight, "check_cloud_transcription_key", lambda backend: {"ok": False, "detail": "not set"}
    )

    result = preflight.run_preflight("eng", "medium", whisper_backend="openai")

    assert result["ok"] is True
    assert result["checks"]["cloud_transcription_key"]["ok"] is False


def test_run_preflight_defaults_to_local_backend_when_unspecified(monkeypatch):
    # backward-compat: existing callers that don't pass whisper_backend at
    # all (there weren't any before Feature C) still get the local check.
    _stub_common_checks(monkeypatch)
    monkeypatch.setattr(preflight, "check_whisper_model", lambda model: {"ok": True, "detail": "cached"})

    result = preflight.run_preflight("eng", "medium")

    assert "whisper_model" in result["checks"]
