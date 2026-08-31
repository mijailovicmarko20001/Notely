#!/usr/bin/env python3
"""
Stage [5] Transcript Segmentation

Assigns transcript segments to slides based on timeline windows, handles boundary
cases, merges short-dwell runs into their chronologically adjacent neighbor, and
consolidates every run of the same slide_number (e.g. a revisited slide) into one
output entry per unique slide.

Also attaches worked-example candidates detected in stage 4
(output/slide_timelines/<lecture_id>_examples.json, optional) to the
consolidated slide entry they happened during, enriched with the surrounding
transcript context and any spoken example cues found in it -- confirmation
and captioning of these candidates happens downstream in stage 6.

Input:
  - output/slide_timelines/<lecture_id>.json
  - output/slide_timelines/<lecture_id>_examples.json (optional)
  - output/transcripts/<lecture_id>.json
  - output/slides_extracted/<lecture_id>.json

Output:
  - output/segmented_transcripts/<lecture_id>.json
"""

import sys
import argparse
from pathlib import Path

# Only needed to bootstrap the `from notely...` import below (finding
# notely/ on sys.path) -- notely.paths.PROJECT_ROOT is the same value and
# is what get_project_root() below returns.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parent.parent

# notely/ (paths) lives alongside scripts/ and webui/ at the project root,
# not on sys.path by default when this file is run directly (python
# scripts/05_segment_transcript.py) -- same fix tests/conftest.py applies
# for test discovery. Must happen before the `from notely...` import below.
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from notely.io import load_json, save_json  # noqa: E402
from notely.paths import PROJECT_ROOT  # noqa: E402
from notely.text import fold_diacritics  # noqa: E402


def get_project_root():
    """Return the project root directory (parent of scripts/)."""
    return PROJECT_ROOT


def assign_transcript_to_runs(timeline, transcript_segments):
    """
    Assign each transcript segment to the specific *timeline run* (by index
    into `timeline`) whose window contains it, not just its slide_number.

    This distinction matters because a slide_number can appear in more than
    one non-adjacent run (the professor revisiting an earlier slide, per the
    sequential-order matcher's backward-jump handling in stage 4) -- each
    run should only pick up the transcript actually spoken during ITS OWN
    window, not the combined transcript from every visit to that slide
    across the whole lecture.

    A segment straddling a boundary is assigned to the window with majority
    overlap. Windows with end=null extend to infinity.

    Returns dict mapping run_index -> list of transcript segment texts.
    """
    run_transcripts = {}

    for segment in transcript_segments:
        seg_start = segment["start"]
        seg_end = segment["end"]
        seg_text = segment["text"]

        best_run = None
        best_overlap = 0

        for idx, slide_entry in enumerate(timeline):
            slide_start = slide_entry["start"]
            slide_end = slide_entry["end"]

            # Handle end=null (extends to infinity)
            if slide_end is None:
                slide_end = float("inf")

            # Calculate overlap
            overlap_start = max(seg_start, slide_start)
            overlap_end = min(seg_end, slide_end)

            if overlap_end > overlap_start:
                overlap = overlap_end - overlap_start
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_run = idx

        if best_run is not None:
            run_transcripts.setdefault(best_run, []).append(seg_text)

    return run_transcripts


def build_slide_text(slide):
    """Combine title and body_text into a single slide_text string."""
    parts = []
    if slide.get("title"):
        parts.append(slide["title"])
    if slide.get("body_text"):
        parts.append(slide["body_text"])
    return "\n".join(parts)


def merge_short_dwell_runs(timeline, run_transcripts, min_dwell=5.0):
    """
    Merge timeline runs with dwell time < min_dwell into the chronologically
    adjacent run (prefer the next run, else the previous one), by list
    position rather than by slide_number comparison.

    Chronological adjacency, not slide-number adjacency, is what "the
    professor flicked past this" actually means -- the old slide-number-based
    neighbor search could skip over several chronologically-later runs to
    find one with a numerically greater slide_number, which may sit far away
    in time (e.g. after a later backward-jump revisit of an earlier slide).

    Returns (run_transcripts, merges, removed_run_indices):
      - run_transcripts: updated in place, absorbed runs removed
      - merges: {surviving_run_index: [absorbed_slide_number, ...]}
      - removed_run_indices: set of run indices merged away
    """
    n = len(timeline)
    merges = {}
    removed = set()

    for idx, slide_entry in enumerate(timeline):
        slide_end = slide_entry["end"]
        if slide_end is None:
            continue  # open-ended run, never a short-dwell candidate

        dwell_time = slide_end - slide_entry["start"]
        if dwell_time >= min_dwell:
            continue

        target = next((j for j in range(idx + 1, n) if j not in removed), None)
        if target is None:
            target = next((j for j in range(idx - 1, -1, -1) if j not in removed), None)
        if target is None:
            continue  # only run in the timeline; nothing to merge into

        this_run_text = run_transcripts.pop(idx, [])
        existing = run_transcripts.get(target, [])
        # Keep chronological order regardless of which side the target is on.
        run_transcripts[target] = this_run_text + existing if idx < target else existing + this_run_text

        removed.add(idx)
        merges.setdefault(target, []).append(slide_entry["slide_number"])

    return run_transcripts, merges, removed


def consolidate_by_slide_number(timeline, run_transcripts, removed, merges):
    """
    Group surviving timeline runs by slide_number into one output entry per
    unique slide -- per CLAUDE.md's stage-5 contract ("Output: per-slide
    {slide_number, slide_text, transcript_text}"), a slide the professor
    revisits later in the lecture (a legitimate backward jump per stage 4)
    should produce ONE consolidated note, not a separate near-duplicate note
    each time the matcher lands on it again.

    Output ordering follows first-seen order in the timeline, which tracks
    the sequential-order assumption closely enough in practice (a revisit
    doesn't reorder the deck, it just adds another chunk of transcript to an
    already-seen slide).

    Returns an ordered list of dicts:
        {slide_number, transcript_text, start, end, windows, merged_from,
         frame_image_path}
    where start/end are the first window's start and the last window's end
    (informational only), `windows` lists every [start, end] pair that
    contributed, so the true total dwell time is recoverable if needed, and
    `frame_image_path` is the last non-null `last_frame_image_path` across
    every contributing run (chronologically last, since a slide revisited
    later may have more/different annotations than its first appearance) --
    or None if no run carried one (e.g. a timeline from before stage 4
    started tracking this).
    """
    order = []
    by_slide = {}

    for idx, slide_entry in enumerate(timeline):
        if idx in removed:
            continue
        slide_num = slide_entry["slide_number"]
        if slide_num not in by_slide:
            by_slide[slide_num] = {
                "windows": [],
                "chunks": [],
                "merged_from": [],
                "frame_image_path": None,
            }
            order.append(slide_num)

        entry = by_slide[slide_num]
        entry["windows"].append([slide_entry["start"], slide_entry["end"]])
        chunk = "\n".join(run_transcripts.get(idx, []))
        if chunk:
            entry["chunks"].append(chunk)
        entry["merged_from"].extend(merges.get(idx, []))
        frame_path = slide_entry.get("last_frame_image_path")
        if frame_path:
            entry["frame_image_path"] = frame_path  # chronologically last wins

    consolidated = []
    for slide_num in order:
        entry = by_slide[slide_num]
        consolidated.append(
            {
                "slide_number": slide_num,
                "transcript_text": "\n\n".join(entry["chunks"]),
                "start": entry["windows"][0][0],
                "end": entry["windows"][-1][1],
                "windows": entry["windows"],
                "merged_from": entry["merged_from"],
                "frame_image_path": entry["frame_image_path"],
            }
        )

    return consolidated


def build_canonical_slide_map(slides_data):
    """slide_number -> first slide_number with identical normalized text."""
    import hashlib
    import re

    def norm(s):
        return re.sub(r"\s+", " ", (s or "").lower()).strip()

    canonical = {}
    first_by_hash = {}
    for slide in slides_data:
        key = hashlib.md5(
            (norm(slide.get("title", "")) + "|" + norm(slide.get("body_text", ""))).encode()
        ).hexdigest()
        if key not in first_by_hash:
            first_by_hash[key] = slide["slide_number"]
        canonical[slide["slide_number"]] = first_by_hash[key]
    return canonical


# Spoken cues that suggest the professor is working through a concrete
# example, in the transcript's language (this project's course content is
# Serbian, hence the Serbian-latin cues alongside the English ones). Stored
# ASCII-folded (see fold()) so a diacritic spelling in the transcript
# ("vežbanje") still matches the stored cue ("vezb").
EXAMPLE_CUES = (
    "primer",
    "na primer",
    "recimo",
    "zadatak",
    "vezb",
    "uradimo",
    "izracunajmo",
    "posmatrajmo",
    "example",
    "for example",
    "let's work",
    "worked example",
    "exercise",
    "suppose",
)


def fold(s):
    """Lowercase and strip diacritics (see notely.text.fold_diacritics) so
    cue matching is accent-insensitive -- Serbian-latin text can spell the
    same word with or without diacritics (vežbanje / vezbanje), and ASR
    transcripts are inconsistent about which one they emit."""
    return fold_diacritics(s).lower()


def transcript_context_for(timestamp, segments, before=20.0, after=40.0):
    """Concatenate transcript segment text overlapping the window
    [timestamp - before, timestamp + after], in transcript order.

    Wider after than before: a professor typically narrates an example
    while or after writing it, not much beforehand, so the window is
    asymmetric toward what's said during/just after the candidate frame.
    """
    window_start = timestamp - before
    window_end = timestamp + after
    parts = [
        segment["text"]
        for segment in segments
        if segment["end"] >= window_start and segment["start"] <= window_end
    ]
    return " ".join(parts).strip()


def score_example_cues(context):
    """Return every EXAMPLE_CUES entry found (as a substring, after
    fold()ing both sides) in `context`, in EXAMPLE_CUES order. A signal fed
    into stage 6's confirmation prompt, not a hard filter -- a candidate
    with zero cue hits can still be a real example (a silent derivation),
    and one with a hit can still be a false positive."""
    folded = fold(context)
    return [cue for cue in EXAMPLE_CUES if cue in folded]


def attach_example_candidates(
    examples_data, output, canonical, transcript_segments, context_before=20.0, context_after=40.0
):
    """
    Attach each stage-4 example candidate to the consolidated output entry
    it happened during, enriched with `transcript_context` and `cue_hits`.

    Attachment, in order:
      1. Canonicalize the candidate's slide_number through the same
         duplicate-slide map (`canonical`) used for the timeline, so a
         candidate captured on a duplicate copy of a slide lands on the
         same output entry its transcript did.
      2. If that slide number has a surviving output entry, attach there.
      3. Otherwise (its run was merged away by merge_short_dwell_runs),
         attach to the output entry whose `windows` contain the
         candidate's timestamp.
      4. Otherwise, attach to the output entry nearest in time (by its
         first window's start) -- a last resort so a candidate is never
         silently dropped just because its home run vanished.

    Mutates `output` in place: every entry gets an `example_candidates` key
    (empty list if none attached), for a consistent shape downstream.
    """
    for entry in output:
        entry.setdefault("example_candidates", [])

    if not examples_data or not output:
        return

    slide_to_index = {entry["slide_number"]: i for i, entry in enumerate(output)}

    for candidate in examples_data:
        slide_num = canonical.get(candidate["slide_number"], candidate["slide_number"])
        idx = slide_to_index.get(slide_num)

        timestamp = candidate["timestamp"]
        if idx is None:
            idx = next(
                (
                    i
                    for i, entry in enumerate(output)
                    if any(
                        w[0] <= timestamp <= (w[1] if w[1] is not None else float("inf"))
                        for w in entry["windows"]
                    )
                ),
                None,
            )
        if idx is None:
            idx = min(range(len(output)), key=lambda i: abs(output[i]["windows"][0][0] - timestamp))

        context = transcript_context_for(timestamp, transcript_segments, context_before, context_after)
        enriched = {**candidate, "transcript_context": context, "cue_hits": score_example_cues(context)}
        output[idx]["example_candidates"].append(enriched)


def segment_transcript(lecture_id, min_dwell=5.0, force=False):
    """Main segmentation logic."""
    project_root = get_project_root()

    # Input paths
    timeline_path = project_root / "output" / "slide_timelines" / f"{lecture_id}.json"
    examples_path = project_root / "output" / "slide_timelines" / f"{lecture_id}_examples.json"
    transcript_path = project_root / "output" / "transcripts" / f"{lecture_id}.json"
    slides_path = project_root / "output" / "slides_extracted" / f"{lecture_id}.json"

    # Output path
    output_path = project_root / "output" / "segmented_transcripts" / f"{lecture_id}.json"

    # Cached output is a successful no-op, not a failure — the orchestrator
    # must be able to re-run the pipeline without --force aborting here.
    if output_path.exists() and not force:
        print(f"[skip] {lecture_id}: {output_path} already exists (use --force to redo)")
        return True

    # Load inputs
    try:
        timeline_data = load_json(timeline_path)
        transcript_data = load_json(transcript_path)
        slides_data = load_json(slides_path)
    except FileNotFoundError as e:
        print(f"Error: missing input file: {e}", file=sys.stderr)
        return False

    # Example candidates are optional: absent for timelines produced before
    # this feature landed, or when stage 4 was run with --no-examples.
    try:
        examples_data = load_json(examples_path).get("candidates", [])
    except FileNotFoundError:
        examples_data = []

    # Extract timeline
    timeline = timeline_data.get("timeline", [])
    transcript_segments = transcript_data.get("segments", [])

    # Canonicalize duplicate slides. Pooled/merged decks can contain many
    # near-identical copies of the same slide (observed: 66% duplicates in a
    # 936-page merged deck), and the matcher may smear one lecture across
    # several copies. Remap every slide number to the first copy with the
    # same normalized text, so consolidation below merges their transcripts.
    canonical = build_canonical_slide_map(slides_data)
    remapped = 0
    for entry in timeline:
        canon = canonical.get(entry["slide_number"], entry["slide_number"])
        if canon != entry["slide_number"]:
            entry["slide_number"] = canon
            remapped += 1
    if remapped:
        print(f"[{lecture_id}] canonicalized {remapped} timeline entr(ies) pointing at duplicate slides")

    # Build slide data map for quick lookup
    slide_data_map = {slide["slide_number"]: slide for slide in slides_data}

    # Assign transcript segments to the specific timeline run they overlap
    run_transcripts = assign_transcript_to_runs(timeline, transcript_segments)

    # Merge short-dwell runs into their chronologically adjacent neighbor
    run_transcripts, merges, removed = merge_short_dwell_runs(timeline, run_transcripts, min_dwell=min_dwell)

    # Consolidate all surviving runs of the same slide_number (e.g. a
    # revisited slide) into one output entry per unique slide
    consolidated = consolidate_by_slide_number(timeline, run_transcripts, removed, merges)

    # Build output
    output = []
    for entry in consolidated:
        slide_num = entry["slide_number"]
        slide_data = slide_data_map.get(slide_num, {})

        output.append(
            {
                "slide_number": slide_num,
                "slide_text": build_slide_text(slide_data),
                "notes_text": slide_data.get("notes_text", ""),
                "transcript_text": entry["transcript_text"],
                "start": entry["start"],
                "end": entry["end"],
                "windows": entry["windows"],
                "merged_from": entry["merged_from"],
                "frame_image_path": entry.get("frame_image_path"),
            }
        )

    # Attach stage-4 example candidates to the entry they happened during.
    attach_example_candidates(examples_data, output, canonical, transcript_segments)

    # Save output
    save_json(output_path, output)
    print(f"Segmented transcript written to {output_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Stage [5] Transcript Segmentation")
    parser.add_argument("lecture_id", nargs="?", help="Lecture ID (e.g., lecture01)")
    parser.add_argument("--all", action="store_true", help="Process all lectures from input/video_urls.json")
    parser.add_argument(
        "--min-dwell",
        type=float,
        default=5.0,
        help="Minimum dwell time in seconds; shorter slides are merged (default: 5.0)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing output")

    args = parser.parse_args()

    project_root = get_project_root()

    if args.all:
        # Load lecture list from video_urls.json
        urls_path = project_root / "input" / "video_urls.json"
        try:
            urls_data = load_json(urls_path)
            lecture_ids = sorted(urls_data.keys())
        except FileNotFoundError:
            print(f"Error: {urls_path} not found", file=sys.stderr)
            sys.exit(1)
    else:
        if not args.lecture_id:
            parser.print_help()
            sys.exit(1)
        lecture_ids = [args.lecture_id]

    failed = []
    for lid in lecture_ids:
        success = segment_transcript(lid, min_dwell=args.min_dwell, force=args.force)
        if not success:
            failed.append(lid)

    if failed:
        print(f"Failed for lectures: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
