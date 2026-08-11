#!/usr/bin/env python3
"""
Stage [7] Assembly

Concatenates per-lecture note files into a single study guide with a table
of contents, in sorted lecture order.

Input:
  - output/notes/<lecture_id>.md (multiple files)

Output:
  - output/study_guide.md

Optional cross-lecture LLM pass left as TODO.
"""

import sys
import json
import os
import argparse
from pathlib import Path


def get_project_root():
    """Return the project root directory (parent of scripts/)."""
    return Path(__file__).parent.parent


def load_json(path):
    """Load JSON from file."""
    with open(path) as f:
        return json.load(f)


def find_lecture_notes(project_root):
    """Find all lecture note files in output/notes/ and return sorted."""
    notes_dir = project_root / 'output' / 'notes'
    if not notes_dir.exists():
        return []

    # Find all .md files matching lecture pattern
    note_files = sorted(notes_dir.glob('*.md'))
    return note_files


def parse_lecture_id_from_filename(filename):
    """Extract lecture ID from filename (e.g., 'lecture01.md' -> 'lecture01')."""
    return filename.stem


def read_lecture_notes(path):
    """Read a lecture note file, fixing image paths for the guide's location.

    Notes live in output/notes/ and embed slide images as
    ../slides_extracted/...; the assembled guide lives one level up in
    output/, where the same images are at slides_extracted/...
    """
    with open(path) as f:
        return f.read().replace("](../slides_extracted/", "](slides_extracted/")


def build_table_of_contents(lectures):
    """Build a markdown table of contents from lecture list."""
    lines = ['# Table of Contents\n']
    for lecture_id in lectures:
        lines.append(f'- [{lecture_id}](#{lecture_id})')
    lines.append('')
    return '\n'.join(lines)


def assemble_guide(force=False):
    """Main assembly logic."""
    project_root = get_project_root()

    output_path = project_root / 'output' / 'study_guide.md'

    # Check if output exists and --force not set
    if output_path.exists() and not force:
        print(f"Output already exists: {output_path}. Use --force to overwrite.", file=sys.stderr)
        return False

    # Find all lecture notes
    note_files = find_lecture_notes(project_root)
    if not note_files:
        print("Warning: no lecture notes found in output/notes/", file=sys.stderr)
        return False

    # Build lecture list and load content
    lectures = []
    content_parts = []

    for note_file in note_files:
        lecture_id = parse_lecture_id_from_filename(note_file)
        lectures.append(lecture_id)

        try:
            note_content = read_lecture_notes(note_file)
            # Note files begin with their own "# lectureNN" H1 — only add a
            # header if one is missing (a duplicate H1 makes PDF export emit
            # a near-blank page per lecture via page-break-before).
            if not note_content.lstrip().startswith(f"# {lecture_id}"):
                content_parts.append(f'# {lecture_id}\n')
            content_parts.append(note_content)
            content_parts.append('')  # Blank line between lectures
        except Exception as e:
            print(f"Error reading {note_file}: {e}", file=sys.stderr)
            return False

    # Build final guide with TOC
    toc = build_table_of_contents(lectures)
    full_guide = toc + '\n'.join(content_parts)

    # Write output. Temp file + atomic rename: a killed process can never
    # leave a truncated-but-non-empty study_guide.md that a later run's
    # exists()-and-nonempty skip check would wrongly trust as done.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = output_path.with_name(f"{output_path.name}.tmp{os.getpid()}")
    with open(tmp_output, 'w') as f:
        f.write(full_guide)
    tmp_output.replace(output_path)

    print(f"Study guide assembled into {output_path}")
    print(f"Included {len(lectures)} lectures: {', '.join(lectures)}")

    # TODO: Optional second LLM pass to build cross-lecture topic index/summary
    # This would involve:
    #   1. Batch-processing the assembled guide through Claude API
    #   2. Generating a topic index or cross-lecture summary
    #   3. Inserting that into the guide
    # Implementation deferred for now.

    return True


def main():
    parser = argparse.ArgumentParser(description='Stage [7] Assembly - Build final study guide')
    parser.add_argument('--force', action='store_true', help='Overwrite existing output')

    args = parser.parse_args()

    success = assemble_guide(force=args.force)
    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
