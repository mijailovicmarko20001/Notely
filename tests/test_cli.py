"""Contract test for notely.cli, written before the implementation
(Phase 4, "new structure").

Every stage script's main() accepts either a single <lecture_id>
positional or --all, never both, never neither -- a two-line
`bool(args.all) == bool(args.lecture_id)` check duplicated across all 7
scripts (three of which -- 00, 02, 05 -- didn't actually have it, see the
`test_lecture_id_all_mutually_exclusive.py` bug fix this refactor builds
on). notely.cli.require_lecture_id_or_all is the one implementation every
script's main() calls instead."""

import argparse

import pytest

from notely.cli import require_lecture_id_or_all


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("lecture_id", nargs="?")
    parser.add_argument("--all", action="store_true")
    return parser


def test_neither_given_errors():
    parser = _parser()
    args = parser.parse_args([])
    with pytest.raises(SystemExit) as exc_info:
        require_lecture_id_or_all(parser, args)
    assert exc_info.value.code != 0


def test_both_given_errors():
    parser = _parser()
    args = parser.parse_args(["lecture01", "--all"])
    with pytest.raises(SystemExit) as exc_info:
        require_lecture_id_or_all(parser, args)
    assert exc_info.value.code != 0


def test_lecture_id_only_is_accepted():
    parser = _parser()
    args = parser.parse_args(["lecture01"])
    require_lecture_id_or_all(parser, args)  # must not raise


def test_all_only_is_accepted():
    parser = _parser()
    args = parser.parse_args(["--all"])
    require_lecture_id_or_all(parser, args)  # must not raise
