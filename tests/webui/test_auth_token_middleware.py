"""Characterization tests for webui/main.py's auth_token_check middleware
(previously zero coverage): the opt-in NOTELY_AUTH_TOKEN shared-secret guard.

_AUTH_TOKEN is read from the environment once at webui.main's import time
(main.py:77), so it can't be flipped per-test via env vars without reloading
the module -- which would rebuild the real app's middleware stack and static
mounts the session-scoped `client` fixture depends on (see
tests/webui/conftest.py's docstring). Instead, this mounts the exact same
middleware *function* on a tiny throwaway app and monkeypatches the module
attribute it reads (webui.main._AUTH_TOKEN) directly -- exercising the real
production code, isolated from the shared app instance."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from webui import main as webui_main


def _app_with_token(monkeypatch, token):
    monkeypatch.setattr(webui_main, "_AUTH_TOKEN", token)
    app = FastAPI()
    app.middleware("http")(webui_main.auth_token_check)

    @app.get("/api/state")
    def state():
        return {"ok": True}

    @app.get("/api/jobs/current/events")
    def events():
        return {"ok": True}

    @app.get("/")
    def index():
        return {"ok": True}

    return TestClient(app)


def test_no_token_configured_is_a_no_op(monkeypatch):
    client = _app_with_token(monkeypatch, "")
    resp = client.get("/api/state")
    assert resp.status_code == 200


def test_token_configured_rejects_missing_auth_header(monkeypatch):
    client = _app_with_token(monkeypatch, "secret123")
    resp = client.get("/api/state")
    assert resp.status_code == 401


def test_token_configured_rejects_wrong_bearer_token(monkeypatch):
    client = _app_with_token(monkeypatch, "secret123")
    resp = client.get("/api/state", headers={"authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_token_configured_accepts_correct_bearer_token(monkeypatch):
    client = _app_with_token(monkeypatch, "secret123")
    resp = client.get("/api/state", headers={"authorization": "Bearer secret123"})
    assert resp.status_code == 200


def test_non_api_path_is_never_gated(monkeypatch):
    client = _app_with_token(monkeypatch, "secret123")
    resp = client.get("/")
    assert resp.status_code == 200


def test_events_endpoint_accepts_query_param_token(monkeypatch):
    # EventSource (browser SSE client) can't set custom headers, so the
    # SSE endpoint alone also accepts ?token=... -- see main.py:87-89.
    client = _app_with_token(monkeypatch, "secret123")
    resp = client.get("/api/jobs/current/events?token=secret123")
    assert resp.status_code == 200


def test_events_endpoint_rejects_wrong_query_param_token(monkeypatch):
    client = _app_with_token(monkeypatch, "secret123")
    resp = client.get("/api/jobs/current/events?token=wrong")
    assert resp.status_code == 401


def test_query_param_token_is_not_accepted_on_non_events_paths(monkeypatch):
    # the query-param fallback is deliberately scoped to paths ending in
    # /events -- a non-SSE endpoint must still require the header.
    client = _app_with_token(monkeypatch, "secret123")
    resp = client.get("/api/state?token=secret123")
    assert resp.status_code == 401
