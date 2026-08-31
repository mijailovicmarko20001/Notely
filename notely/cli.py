"""Shared argparse pieces for the stage scripts.

Every stage script's main() accepts either a single <lecture_id>
positional or --all, never both, never neither -- a two-line check
duplicated across all 7 scripts, three of which (00, 02, 05) didn't
actually have the "both given" half of it, silently letting --all win
and the given lecture_id be ignored (see
tests/test_lecture_id_all_mutually_exclusive.py)."""

import argparse


def require_lecture_id_or_all(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Call after parser.parse_args(). Exits via parser.error (SystemExit(2))
    if args.lecture_id and args.all aren't exactly one-given."""
    if bool(args.all) == bool(args.lecture_id):
        parser.error("provide exactly one of <lecture_id> or --all")
