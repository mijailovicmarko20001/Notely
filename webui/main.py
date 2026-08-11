"""Notely web UI server.

Run from the repo root:  uvicorn webui.main:app --port 8000
(or:  python -m webui.main)
"""

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import OUTPUT_DIR, PROJECT_ROOT

STATIC_DIR = PROJECT_ROOT / "webui" / "static"

app = FastAPI(title="Notely")
app.include_router(router)


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
