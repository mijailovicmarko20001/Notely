"""POST /api/settings/test-key -- previously hardcoded to Anthropic only
(no `provider` param at all, zero test coverage). Extended to test any of
the three provider keys Feature C added settings for, via a `provider`
query param defaulting to "anthropic" for backward compatibility.

Each provider's SDK is faked via sys.modules injection (same technique
tests/test_cloud_transcribers.py uses) -- no real network call anywhere
in this file.
"""

import sys
import types


def _install_fake_module(monkeypatch, module_name, **attrs):
    fake_module = types.ModuleType(module_name)
    for k, v in attrs.items():
        setattr(fake_module, k, v)
    monkeypatch.setitem(sys.modules, module_name, fake_module)


# --- anthropic (default provider, existing behavior) -------------------------


def test_anthropic_missing_key_returns_400(client, monkeypatch):
    # config.get_api_key() falls back to a real os.environ var if the tmp
    # .env doesn't have one -- and scripts/04_match_frames_to_slides.py
    # loads the real repo's .env at *module import time* (see
    # test_jobs_scheduler.py::project's own comment on this exact leak
    # class), which really does have ANTHROPIC_API_KEY set. Delete it from
    # the real environment explicitly so "missing" is actually missing,
    # regardless of what else has been imported earlier in the session.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post("/api/settings/test-key")
    assert resp.status_code == 400


def test_anthropic_ping_failure_returns_ok_false(client, monkeypatch):
    client.put("/api/settings", json={"ANTHROPIC_API_KEY": "sk-ant-test"})

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("401 unauthorized")

    _install_fake_module(monkeypatch, "anthropic", Anthropic=_FakeClient)

    resp = client.post("/api/settings/test-key")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "error" in body


def test_anthropic_ping_success_returns_ok_true(client, monkeypatch):
    client.put("/api/settings", json={"ANTHROPIC_API_KEY": "sk-ant-test"})

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        class messages:
            @staticmethod
            def create(**kwargs):
                return object()

    _install_fake_module(monkeypatch, "anthropic", Anthropic=_FakeClient)

    resp = client.post("/api/settings/test-key")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_default_provider_is_anthropic(client, monkeypatch):
    # no ?provider= at all -- must behave exactly like provider=anthropic
    client.put("/api/settings", json={"ANTHROPIC_API_KEY": "sk-ant-test"})

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        class messages:
            @staticmethod
            def create(**kwargs):
                return object()

    _install_fake_module(monkeypatch, "anthropic", Anthropic=_FakeClient)

    resp = client.post("/api/settings/test-key", params={})
    assert resp.json() == {"ok": True}


# --- groq ----------------------------------------------------------------------


def test_groq_missing_key_returns_400(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)  # same real-env leak guard as anthropic's
    resp = client.post("/api/settings/test-key", params={"provider": "groq"})
    assert resp.status_code == 400


def test_groq_valid_key_returns_ok(client, monkeypatch):
    client.put("/api/settings", json={"GROQ_API_KEY": "gsk_test"})

    class _FakeModels:
        @staticmethod
        def list():
            return []

    class _FakeClient:
        def __init__(self, **kwargs):
            self.models = _FakeModels()

    _install_fake_module(monkeypatch, "groq", Groq=_FakeClient)

    resp = client.post("/api/settings/test-key", params={"provider": "groq"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_groq_ping_failure_returns_ok_false(client, monkeypatch):
    client.put("/api/settings", json={"GROQ_API_KEY": "gsk_test"})

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        class models:
            @staticmethod
            def list():
                raise RuntimeError("401 unauthorized")

    _install_fake_module(monkeypatch, "groq", Groq=_FakeClient)

    resp = client.post("/api/settings/test-key", params={"provider": "groq"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# --- openai ----------------------------------------------------------------------


def test_openai_missing_key_returns_400(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # same real-env leak guard as anthropic's
    resp = client.post("/api/settings/test-key", params={"provider": "openai"})
    assert resp.status_code == 400


def test_openai_valid_key_returns_ok(client, monkeypatch):
    client.put("/api/settings", json={"OPENAI_API_KEY": "sk-proj-test"})

    class _FakeModels:
        @staticmethod
        def list():
            return []

    class _FakeClient:
        def __init__(self, **kwargs):
            self.models = _FakeModels()

    _install_fake_module(monkeypatch, "openai", OpenAI=_FakeClient)

    resp = client.post("/api/settings/test-key", params={"provider": "openai"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# --- unknown provider ------------------------------------------------------------


def test_unknown_provider_returns_422(client):
    resp = client.post("/api/settings/test-key", params={"provider": "not-a-real-provider"})
    assert resp.status_code == 422
