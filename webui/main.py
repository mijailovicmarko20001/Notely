"""Notely web UI server.

Run from the repo root:  uvicorn webui.main:app --port 8000
(or:  python -m webui.main)
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, middleware
from .errors import NotelyError
from .routes import router

# This file's own directory, not derived from config.PROJECT_ROOT: the
# static assets ship alongside this module regardless of where the
# project root/user-data paths point (tests repoint config.PROJECT_ROOT
# at a tmp dir; the app's own JS/CSS still live here).
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Notely")
app.include_router(router)
middleware.setup(app)


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


config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(config.OUTPUT_DIR)), name="files")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("webui.main:app", host="127.0.0.1", port=8000)
