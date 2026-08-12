"""Notely web UI server.

Run from the repo root:  uvicorn webui.main:app --port 8000
(or:  python -m webui.main)
"""

import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import OUTPUT_DIR, PROJECT_ROOT
from .errors import NotelyError
from .routes import router

STATIC_DIR = PROJECT_ROOT / "webui" / "static"

app = FastAPI(title="Notely")
app.include_router(router)


# A4: every 4xx/5xx from the API -- whether raised as a plain HTTPException
# (still used for simple per-route checks) or a typed NotelyError from a
# service module -- comes back with the same {"error": ...} shape. `detail`
# is kept alongside it only because static/app.js's error handling reads
# `.detail`; new client code should read `.error`.
@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        {"error": exc.detail, "detail": exc.detail},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(NotelyError)
async def _notely_error_handler(request: Request, exc: NotelyError):
    message = str(exc)
    return JSONResponse({"error": message, "detail": message}, status_code=exc.status_code)

# Host header check: blocks DNS-rebinding (a page on evil.com whose DNS
# answer flips to 127.0.0.1, so the browser's same-origin check passes but
# the Host header still says evil.com). NOTELY_ALLOWED_HOSTS lets the Docker
# deployment add its own hostname/IP; defaults cover the plain-localhost case.
_extra_hosts = [h.strip() for h in os.environ.get("NOTELY_ALLOWED_HOSTS", "").split(",") if h.strip()]
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", *_extra_hosts],
)

_ALLOWED_ORIGIN_HOSTS = {"localhost", "127.0.0.1", "::1", *_extra_hosts}


@app.middleware("http")
async def origin_check(request: Request, call_next):
    """Belt-and-suspenders CSRF guard for state-changing requests: a browser
    always sends Origin (or falls back to Referer) on cross-origin fetches,
    so a same-origin (by hostname, like TrustedHostMiddleware above) check
    here blocks a malicious page's POST/PUT/DELETE/PATCH even though there's
    no auth to steal. Absent-header requests (curl, non-browser clients)
    pass through -- this is about browser-borne CSRF, not access control."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin:
            from urllib.parse import urlparse

            host = urlparse(origin).hostname
            if host not in _ALLOWED_ORIGIN_HOSTS:
                return JSONResponse({"detail": "cross-origin request rejected"}, status_code=403)
    return await call_next(request)


_AUTH_TOKEN = os.environ.get("NOTELY_AUTH_TOKEN", "").strip()


@app.middleware("http")
async def auth_token_check(request: Request, call_next):
    """Opt-in shared-token auth for anyone who deliberately exposes this
    beyond localhost (see docker-compose.yml). No-op when NOTELY_AUTH_TOKEN
    isn't set, which keeps the default localhost-only setup frictionless."""
    if _AUTH_TOKEN and request.url.path.startswith("/api"):
        supplied = request.headers.get("authorization", "")
        # EventSource can't set request headers, so the SSE endpoint alone
        # also accepts the token as a query param.
        query_token = request.query_params.get("token", "") if request.url.path.endswith("/events") else ""
        if supplied != f"Bearer {_AUTH_TOKEN}" and query_token != _AUTH_TOKEN:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """The UI is a local dev-style app: stale cached JS against fresh HTML
    breaks click handlers invisibly. Force revalidation on every load."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store"
    return response

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(OUTPUT_DIR)), name="files")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("webui.main:app", host="127.0.0.1", port=8000)
