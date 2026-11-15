"""Trusted issue-reproducing test (pinned by SHA-256 in demo/pinned.json; never part of the candidate tree).

Milestone 1 records this file's identity only. It is not executed until Milestone 2.
"""
from src.labels import is_valid_label, normalize_label


def test_trims_and_collapses_whitespace():
    assert normalize_label("  Bug   Report ") == "bug report"


def test_whitespace_only_label_is_invalid():
    assert is_valid_label(" \t ") is False


def test_existing_behaviour_kept():
    assert normalize_label("ABC") == "abc"
