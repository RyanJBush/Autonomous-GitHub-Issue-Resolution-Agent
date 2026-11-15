"""Synthetic demonstration module: label normalization."""


def normalize_label(text: str) -> str:
    """Return a canonical label: lower-case, trimmed, internal whitespace collapsed to one space."""
    return ' '.join(text.split()).lower()


def is_valid_label(text: str) -> bool:
    """A valid label is non-empty after normalization."""
    return normalize_label(text) != ""
