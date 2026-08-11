"""Stage-4 review data and manual corrections.

The needs_review file is advisory only; the pipeline's source of truth is
output/slide_timelines/<id>.json. Corrections edit that file (mirroring the
manual fixes done for lecture01) and stage 05+ is re-run with --force.
"""

import json
import os
import time

from .config import OUTPUT_DIR


def _load(path):
    with open(path) as f:
        return json.load(f)


def get_review_data(lecture_id: str) -> dict:
    timeline_path = OUTPUT_DIR / "slide_timelines" / f"{lecture_id}.json"
    review_path = OUTPUT_DIR / "slide_timelines" / f"{lecture_id}_needs_review.json"
    slides_path = OUTPUT_DIR / "slides_extracted" / f"{lecture_id}.json"
    if not timeline_path.exists():
        raise FileNotFoundError(f"no timeline for {lecture_id} — run stage 4 first")

    timeline = _load(timeline_path)
    review = _load(review_path) if review_path.exists() else {
        "low_confidence_matches": [], "unmatched_slides": [], "backward_jumps": []
    }
    slides = _load(slides_path) if slides_path.exists() else []
    slide_meta = [
        {
            "slide_number": s["slide_number"],
            "title": s.get("title") or "",
            "image": f"/files/slides_extracted/{lecture_id}_images/slide_{s['slide_number']:03d}.png",
        }
        for s in slides
    ]

    def frame_url(p):
        # stored PROJECT_ROOT-relative like "output/frame_events/…"; /files/ serves output/
        return "/files/" + p.removeprefix("output/") if p else None

    for item in review.get("low_confidence_matches", []):
        item["frame_url"] = frame_url(item.get("frame_image_path", ""))
    for item in review.get("backward_jumps", []):
        item["frame_url"] = frame_url(item.get("frame_image_path", ""))

    return {
        "lecture_id": lecture_id,
        "timeline": timeline.get("timeline", []),
        "notes": timeline.get("notes", []),
        "review": review,
        "slides": slide_meta,
    }


def apply_corrections(lecture_id: str, corrections: list) -> dict:
    """corrections: [{timestamp, slide_number|null}] — null drops the entry
    (its window merges into the previous entry, matching how the lecture01
    manual fixes were done)."""
    timeline_path = OUTPUT_DIR / "slide_timelines" / f"{lecture_id}.json"
    data = _load(timeline_path)
    timeline = data.get("timeline", [])
    notes = data.setdefault("notes", [])
    applied = 0

    for corr in corrections:
        ts = float(corr["timestamp"])
        target = corr.get("slide_number")
        idx = next(
            (i for i, e in enumerate(timeline) if e["start"] <= ts < e["end"]),
            None,
        )
        if idx is None:
            continue
        entry = timeline[idx]
        if target is None:
            # merge this window into the previous entry (or next, if first)
            if idx > 0:
                timeline[idx - 1]["end"] = entry["end"]
            elif len(timeline) > 1:
                timeline[idx + 1]["start"] = entry["start"]
            removed = timeline.pop(idx)
            notes.append(
                f"Web-UI correction {time.strftime('%Y-%m-%d')}: dropped slide "
                f"{removed['slide_number']} @{removed['start']}-{removed['end']} (merged into neighbor)."
            )
        else:
            old = entry["slide_number"]
            entry["slide_number"] = int(target)
            entry["confidence"] = 1.0
            notes.append(
                f"Web-UI correction {time.strftime('%Y-%m-%d')}: slide {old} -> {target} "
                f"@{entry['start']}-{entry['end']} (manual)."
            )
        applied += 1

    # merge adjacent entries that now share a slide number
    merged = []
    for e in timeline:
        if merged and merged[-1]["slide_number"] == e["slide_number"] and merged[-1]["end"] == e["start"]:
            merged[-1]["end"] = e["end"]
            merged[-1]["confidence"] = max(merged[-1]["confidence"], e["confidence"])
        else:
            merged.append(e)
    data["timeline"] = merged

    # Temp file + atomic rename: a killed process (e.g. the server restarts
    # mid-request) can never leave a truncated-but-non-empty timeline that
    # stage 4's own exists()-and-nonempty skip check would wrongly trust.
    tmp_path = timeline_path.with_name(f"{timeline_path.name}.tmp{os.getpid()}")
    with open(tmp_path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(timeline_path)
    return {"applied": applied, "timeline_entries": len(merged)}
