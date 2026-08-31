"""Lecture sources and slide decks -- the web UI's "Sources" tab: playlist
expansion, saving the ordered lecture list, and the three deck-upload flows
(pool, suggest, per-lecture). Also the video preview-frame grab used by the
crop-region picker, since that's tuning input for the same setup flow."""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from notely.io import save_json
from .. import config, decks, lecture_match, media, playlist, preflight
from ..models import LectureEntries, PlaylistExpandRequest
from .common import load_json, validated_lecture_id

router = APIRouter(tags=["slides"])


@router.post("/playlist/expand")
def expand(body: PlaylistExpandRequest):
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "url required")
    try:
        return {"entries": playlist.expand_playlist(url)}
    except playlist.PlaylistError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/lectures")
def set_lectures(body: LectureEntries):
    """body.entries: ordered [{title, url}] — assigns lecture01..NN."""
    urls, meta = {}, {}
    for i, e in enumerate(body.entries, start=1):
        lecture_id = f"lecture{i:02d}"
        try:
            urls[lecture_id] = playlist.validate_video_url(e.url)
        except playlist.PlaylistError as err:
            raise HTTPException(422, f"entry {i}: {err}") from err
        meta[lecture_id] = {"title": e.title or lecture_id}
    config.INPUT_DIR.mkdir(parents=True, exist_ok=True)
    save_json(config.VIDEO_URLS_PATH, urls)
    existing_meta = load_json(config.LECTURES_META_PATH, {})
    for k, v in meta.items():
        existing_meta.setdefault(k, {}).update(v)
    save_json(config.LECTURES_META_PATH, existing_meta)
    return {"ok": True, "lectures": list(urls)}


@router.post("/slides/upload-pool")
async def upload_pool(files: list[UploadFile] = File(...)):
    """'I don't know which deck covers which lecture' mode: save all decks to a
    pool, merge them into one combined PDF, and give every lecture that same
    combined deck. Stage 4's content matching then figures out per video which
    slides were actually shown — unshown slides are simply never matched
    (already the normal case, since decks can span lectures).

    This upload *replaces* the pool rather than adding to it: the whole set of
    decks for the course goes up in one request, and leftovers from a previous
    course must not stay in the merge."""
    urls = load_json(config.VIDEO_URLS_PATH, {})
    if not urls:
        raise HTTPException(400, "add your lectures first — the combined deck is copied to each one")

    await decks.save_pool_uploads(files)
    result = decks.merge_pool()
    decks.distribute_pool_deck(result["merged_path"], urls)

    return {
        "ok": True,
        "pool_decks": [p.name for p in result["pool_files"]],
        "merged_pages": result["total_pages"],
        "scanned_pages": result["scanned_pages"],
        "duplicate_pages_skipped": result["scanned_pages"] - result["total_pages"],
        "lectures": sorted(urls),
    }


@router.post("/slides/suggest")
async def suggest_slides(file: UploadFile = File(...)):
    """Suggest which lecture(s) a deck belongs to by comparing its first-slide
    text against the video titles. A pre-fill for the UI dropdown — never a
    silent decision."""
    suggestions = await lecture_match.suggest_lectures_for_upload(file)
    return {"suggestions": suggestions}


@router.post("/slides/upload")
async def upload_slides(file: UploadFile = File(...), lecture_ids: str = Form(...)):
    """One deck may cover several lectures: copy it under each lecture's name."""
    ids = [validated_lecture_id(x.strip()) for x in lecture_ids.split(",") if x.strip()]
    if not ids:
        raise HTTPException(400, "lecture_ids required")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext == "pptx" and not preflight.check_soffice()["ok"]:
        raise HTTPException(422, "LibreOffice not available — convert this deck to PDF first")
    saved = await decks.save_deck_for_lectures(file, ids)
    return {"ok": True, "saved": saved}


@router.get("/lectures/{lecture_id}/preview-frame")
def preview_frame(lecture_id: str, t: float = 60.0):
    """One frame from the lecture's own video, for the crop-region picker."""
    lecture_id = validated_lecture_id(lecture_id)
    tmp_path = media.grab_preview_frame(lecture_id, t)
    return FileResponse(
        tmp_path,
        media_type="image/jpeg",
        background=BackgroundTask(lambda: tmp_path.unlink(missing_ok=True)),
    )
