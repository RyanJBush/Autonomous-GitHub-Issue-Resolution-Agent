"""Synthetic public test inside the demo target. Candidate patches may never modify this file."""
from src.labels import normalize_label


def test_lowercases():
    assert normalize_label("ABC") == "abc"
