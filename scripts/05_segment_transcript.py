#!/usr/bin/env python3
"""
Stage [5] Transcript Segmentation

Assigns transcript segments to slides based on timeline windows, handles boundary
cases, merges short-dwell runs into their chronologically adjacent neighbor, and
consolidates every run of the same slide_number (e.g. a revisited slide) into one
output entry per unique slide.

Input:
  - output/slide_timelines/<lecture_id>.json
  - output/transcripts/<lecture_id>.json
  - output/slides_extracted/<lecture_id>.json

Output:
  - output/segmented_transcripts/<lecture_id>.json
"""

import json
import sys
import argparse
from pathlib import Path


def get_project_root():
    """Return the project root directory (parent of scripts/)."""
    return Path(__file__).parent.parent


def load_json(path):
    """Load JSON from file."""
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    """Save JSON to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


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
        seg_start = segment['start']
        seg_end = segment['end']
        seg_text = segment['text']

        best_run = None
        best_overlap = 0

        for idx, slide_entry in enumerate(timeline):
            slide_start = slide_entry['start']
            slide_end = slide_entry['end']

            # Handle end=null (extends to infinity)
            if slide_end is None:
                slide_end = float('inf')

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
    if slide.get('title'):
        parts.append(slide['title'])
    if slide.get('body_text'):
        parts.append(slide['body_text'])
    return '\n'.join(parts)


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
        slide_end = slide_entry['end']
        if slide_end is None:
            continue  # open-ended run, never a short-dwell candidate

        dwell_time = slide_end - slide_entry['start']
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
        run_transcripts[target] = (
            this_run_text + existing if idx < target else existing + this_run_text
        )

        removed.add(idx)
        merges.setdefault(target, []).append(slide_entry['slide_number'])

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
        {slide_number, transcript_text, start, end, windows, merged_from}
    where start/end are the first window's start and the last window's end
    (informational only), and `windows` lists every [start, end] pair that
    contributed, so the true total dwell time is recoverable if needed.
    """
    order = []
    by_slide = {}

    for idx, slide_entry in enumerate(timeline):
        if idx in removed:
            continue
        slide_num = slide_entry['slide_number']
        if slide_num not in by_slide:
            by_slide[slide_num] = {
                "windows": [],
                "chunks": [],
                "merged_from": [],
            }
            order.append(slide_num)

        entry = by_slide[slide_num]
        entry["windows"].append([slide_entry['start'], slide_entry['end']])
        chunk = '\n'.join(run_transcripts.get(idx, []))
        if chunk:
            entry["chunks"].append(chunk)
        entry["merged_from"].extend(merges.get(idx, []))

    consolidated = []
    for slide_num in order:
        entry = by_slide[slide_num]
        consolidated.append({
            "slide_number": slide_num,
            "transcript_text": '\n\n'.join(entry["chunks"]),
            "start": entry["windows"][0][0],
            "end": entry["windows"][-1][1],
            "windows": entry["windows"],
            "merged_from": entry["merged_from"],
        })

    return consolidated


def build_canonical_slide_map(slides_data):
    """slide_number -> first slide_number with identical normalized text."""
    import hashlib
    import re

    def norm(s):
        return re.sub(r'\s+', ' ', (s or '').lower()).strip()

    canonical = {}
    first_by_hash = {}
    for slide in slides_data:
        key = hashlib.md5(
            (norm(slide.get('title', '')) + '|' + norm(slide.get('body_text', ''))).encode()
        ).hexdigest()
        if key not in first_by_hash:
            first_by_hash[key] = slide['slide_number']
        canonical[slide['slide_number']] = first_by_hash[key]
    return canonical


def segment_transcript(lecture_id, min_dwell=5.0, force=False):
    """Main segmentation logic."""
    project_root = get_project_root()

    # Input paths
    timeline_path = project_root / 'output' / 'slide_timelines' / f'{lecture_id}.json'
    transcript_path = project_root / 'output' / 'transcripts' / f'{lecture_id}.json'
    slides_path = project_root / 'output' / 'slides_extracted' / f'{lecture_id}.json'

    # Output path
    output_path = project_root / 'output' / 'segmented_transcripts' / f'{lecture_id}.json'

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

    # Extract timeline
    timeline = timeline_data.get('timeline', [])
    transcript_segments = transcript_data.get('segments', [])

    # Canonicalize duplicate slides. Pooled/merged decks can contain many
    # near-identical copies of the same slide (observed: 66% duplicates in a
    # 936-page merged deck), and the matcher may smear one lecture across
    # several copies. Remap every slide number to the first copy with the
    # same normalized text, so consolidation below merges their transcripts.
    canonical = build_canonical_slide_map(slides_data)
    remapped = 0
    for entry in timeline:
        canon = canonical.get(entry['slide_number'], entry['slide_number'])
        if canon != entry['slide_number']:
            entry['slide_number'] = canon
            remapped += 1
    if remapped:
        print(f"[{lecture_id}] canonicalized {remapped} timeline entr(ies) pointing at duplicate slides")

    # Build slide data map for quick lookup
    slide_data_map = {slide['slide_number']: slide for slide in slides_data}

    # Assign transcript segments to the specific timeline run they overlap
    run_transcripts = assign_transcript_to_runs(timeline, transcript_segments)

    # Merge short-dwell runs into their chronologically adjacent neighbor
    run_transcripts, merges, removed = merge_short_dwell_runs(
        timeline, run_transcripts, min_dwell=min_dwell
    )

    # Consolidate all surviving runs of the same slide_number (e.g. a
    # revisited slide) into one output entry per unique slide
    consolidated = consolidate_by_slide_number(timeline, run_transcripts, removed, merges)

    # Build output
    output = []
    for entry in consolidated:
        slide_num = entry['slide_number']
        slide_data = slide_data_map.get(slide_num, {})

        output.append({
            'slide_number': slide_num,
            'slide_text': build_slide_text(slide_data),
            'notes_text': slide_data.get('notes_text', ''),
            'transcript_text': entry['transcript_text'],
            'start': entry['start'],
            'end': entry['end'],
            'windows': entry['windows'],
            'merged_from': entry['merged_from'],
        })

    # Save output
    save_json(output_path, output)
    print(f"Segmented transcript written to {output_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description='Stage [5] Transcript Segmentation')
    parser.add_argument('lecture_id', nargs='?', help='Lecture ID (e.g., lecture01)')
    parser.add_argument('--all', action='store_true', help='Process all lectures from input/video_urls.json')
    parser.add_argument('--min-dwell', type=float, default=5.0,
                        help='Minimum dwell time in seconds; shorter slides are merged (default: 5.0)')
    parser.add_argument('--force', action='store_true', help='Overwrite existing output')

    args = parser.parse_args()

    project_root = get_project_root()

    if args.all:
        # Load lecture list from video_urls.json
        urls_path = project_root / 'input' / 'video_urls.json'
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


if __name__ == '__main__':
    main()
