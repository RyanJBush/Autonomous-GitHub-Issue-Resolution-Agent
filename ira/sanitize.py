"""Bound and neutralise untrusted text before it reaches the review packet.

Adapted from the approach in RyanJBush/model-regression-detection-system (mrds/mdsafe.py at 3086d87): control,
format and line-separator characters become visible escapes, values are truncated, and inline/fenced code uses a
fence longer than any backtick run in the content.
"""
from __future__ import annotations

import re
import unicodedata

_UNSAFE = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


def _escape_char(ch: str) -> str:
    return f"\\u{ord(ch):04x}" if ord(ch) <= 0xFFFF else f"\\U{ord(ch):08x}"


def visible(value, max_chars: int) -> str:
    """Single line: every unsafe character (including newline and tab) escaped, then truncated."""
    s = "null" if value is None else str(value)
    s = "".join(_escape_char(c) if c in "\t\r\n" or unicodedata.category(c) in _UNSAFE else c for c in s)
    return s if len(s) <= max_chars else s[:max_chars] + "...(truncated)"


def visible_block(value: str, max_chars: int) -> tuple[str, bool]:
    """Multi-line: keeps LF, escapes every other unsafe character, truncates. Returns (text, truncated)."""
    s = "".join(c if c == "\n" else (_escape_char(c) if c in "\t\r" or unicodedata.category(c) in _UNSAFE else c)
                for c in value)
    return (s, False) if len(s) <= max_chars else (s[:max_chars], True)


def bounded(value: str, max_chars: int) -> tuple[str, bool]:
    """For JSON storage (json.dumps escapes control characters itself): truncate only."""
    return (value, False) if len(value) <= max_chars else (value[:max_chars], True)


def code_span(value, max_chars: int) -> str:
    s = visible(value, max_chars).replace("|", "\\u007c") or "(empty)"
    fence = "`" * (max((len(r) for r in re.findall(r"`+", s)), default=0) + 1)
    pad = " " if s.startswith("`") or s.endswith("`") else ""
    return f"{fence}{pad}{s}{pad}{fence}"


def fenced(text: str, info: str = "") -> str:
    fence = "`" * max(3, max((len(r) for r in re.findall(r"`+", text)), default=0) + 1)
    return f"{fence}{info}\n{text}{'' if text.endswith(chr(10)) else chr(10)}{fence}"
