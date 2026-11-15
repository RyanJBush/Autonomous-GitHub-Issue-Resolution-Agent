"""Strict patch policy (contract item 3). Pure text processing: nothing here runs a process or imports candidate code.

Accepted grammar (narrow on purpose; everything else is rejected):

    [diff --git a/P b/P]          optional, exact
    --- a/P                       exact, no timestamp
    +++ b/P                       exact, no timestamp
    @@ -A[,B] +C[,D] @@[ text]    1..max_hunks hunks, ascending, non-overlapping
    ' ' / '-' / '+' body lines    counts must match the header exactly

P is the single allowlisted path. Hunk positions must match the pinned base file exactly (no offset, no fuzz).
Not supported: extended headers (index, mode, rename, copy, similarity, new/deleted file), /dev/null, binary
patches, "\\ No newline at end of file", CRLF, NUL and other control or format characters.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from . import caps

HUNK_HEADER = re.compile(r"^@@ -(\d{1,6})(?:,(\d{1,6}))? \+(\d{1,6})(?:,(\d{1,6}))? @@(?: .{0,200})?$")

DISALLOWED_HEADERS = (
    ("rename from ", "rename_not_allowed"), ("rename to ", "rename_not_allowed"),
    ("similarity index ", "rename_not_allowed"), ("dissimilarity index ", "rename_not_allowed"),
    ("copy from ", "copy_not_allowed"), ("copy to ", "copy_not_allowed"),
    ("old mode ", "mode_change_not_allowed"), ("new mode ", "mode_change_not_allowed"),
    ("new file mode ", "file_creation_not_allowed"), ("deleted file mode ", "file_deletion_not_allowed"),
    ("index ", "index_header_not_allowed"),
    ("Binary files ", "binary_not_allowed"), ("GIT binary patch", "binary_not_allowed"),
)
CONFIG_NAMES = {"pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "pytest.ini", "conftest.py", "Makefile",
                "MANIFEST.in", ".gitattributes", ".gitignore", ".gitmodules", ".pre-commit-config.yaml"}
CONFIG_SUFFIXES = (".toml", ".cfg", ".ini", ".yaml", ".yml", ".json", ".lock", ".txt")


class PolicyViolation(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple  # ((op, text), ...) with op in " ", "-", "+"


@dataclass(frozen=True)
class ValidatedPatch:
    path: str
    hunks: tuple
    added: int
    removed: int
    patch_bytes: int
    patch_sha256: str
    new_content: bytes
    new_content_sha256: str


def _v(code, detail):
    raise PolicyViolation(code, detail)


def classify_path(raw: str, prefix: str) -> str:
    """Reason code for a header path that is not the allowlisted one."""
    if raw == "/dev/null":
        return "file_creation_not_allowed" if prefix == "a/" else "file_deletion_not_allowed"
    if raw.startswith('"'):
        return "quoted_path_not_allowed"
    if "\t" in raw or " " in raw:
        return "malformed_file_header"
    if not raw.startswith(prefix):
        return "malformed_file_header"
    rel = raw[len(prefix):]
    parts = rel.split("/")
    if (not rel or rel.startswith("/") or "\\" in rel or ":" in rel
            or any(p in ("", ".", "..") for p in parts)):
        return "path_escape"
    name = parts[-1]
    if parts[0] == ".github":
        return "workflow_path_not_allowed"
    if parts[0] in ("tests", "test") or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py":
        return "test_path_not_allowed"
    if name in CONFIG_NAMES or name.endswith(CONFIG_SUFFIXES):
        return "config_path_not_allowed"
    return "path_not_allowed"


def _check_characters(diff: str) -> None:
    if "\0" in diff:
        _v("binary_not_allowed", "NUL byte in diff")
    if "\r" in diff:
        _v("crlf_not_allowed", "carriage return in diff")
    for ch in diff:
        if ch in "\n\t":
            continue
        cat = unicodedata.category(ch)
        if cat == "Cc":
            _v("control_character", f"control character U+{ord(ch):04X}")
        if cat in ("Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            _v("format_character", f"format/unassigned character U+{ord(ch):04X} (e.g. bidi override)")


def parse(diff: str, path: str, limits) -> tuple:
    """Return hunks after grammar, size and character checks. Does not look at the base file."""
    if type(diff) is not str:
        _v("malformed_diff", "diff must be a string")
    size = len(diff.encode("utf-8"))
    if size > limits.max_patch_bytes:
        _v("patch_too_large", f"{size} bytes > {limits.max_patch_bytes}")
    _check_characters(diff)
    if not diff.endswith("\n"):
        _v("missing_final_newline", "diff must end with a newline")
    lines = diff[:-1].split("\n")
    for line in lines:
        if len(line.encode("utf-8")) > caps.MAX_DIFF_LINE_BYTES:
            _v("line_too_long", f"diff line exceeds {caps.MAX_DIFF_LINE_BYTES} bytes")

    # Extended headers anywhere before the first hunk are classified first, so the reason names the operation.
    for line in lines:
        if line.startswith("@@"):
            break
        if line.startswith(("diff --git ", "--- ", "+++ ")):
            continue
        if line in ("new file mode 120000", "new mode 120000", "old mode 120000", "deleted file mode 120000"):
            _v("symlink_not_allowed", "symbolic link modes are not allowed")
        for marker, code in DISALLOWED_HEADERS:
            if line.startswith(marker):
                _v(code, f"extended header '{marker.strip()}' is not allowed")

    i = 0
    if lines[0].startswith("diff --git "):
        if lines[0] != f"diff --git a/{path} b/{path}":
            m = re.match(r"^diff --git (\S+) (\S+)$", lines[0])
            if not m:
                _v("malformed_file_header", "diff --git line is malformed")
            for raw, prefix in ((m.group(1), "a/"), (m.group(2), "b/")):
                if raw != prefix + path:
                    _v(classify_path(raw, prefix), "diff --git names a path other than the allowlisted file")
        i = 1
    while i < len(lines) and not lines[i].startswith("--- "):
        for marker, code in DISALLOWED_HEADERS:
            if lines[i].startswith(marker):
                _v(code, f"extended header '{marker.strip()}' is not allowed")
        if lines[i].startswith("@@"):
            _v("missing_file_header", "hunk before ---/+++ headers")
        _v("malformed_file_header", "unexpected line before the --- header")
    if i + 1 >= len(lines):
        _v("missing_file_header", "--- and +++ headers are required")
    old_hdr, new_hdr = lines[i][4:], lines[i + 1]
    if not new_hdr.startswith("+++ "):
        _v("malformed_file_header", "+++ header must follow --- header")
    new_hdr = new_hdr[4:]
    if old_hdr != f"a/{path}":
        _v(classify_path(old_hdr, "a/"), "--- header is not the allowlisted file")
    if new_hdr != f"b/{path}":
        _v(classify_path(new_hdr, "b/"), "+++ header is not the allowlisted file")
    i += 2

    hunks = []
    if i >= len(lines):
        _v("no_hunks", "diff contains no hunks")
    while i < len(lines):
        line = lines[i]
        if not line.startswith("@@"):
            if line.startswith("diff --git ") or line.startswith("--- ") or line.startswith("+++ "):
                _v("multiple_files_not_allowed", "a second file section is not allowed")
            for marker, code in DISALLOWED_HEADERS:
                if line.startswith(marker):
                    _v(code, f"extended header '{marker.strip()}' is not allowed")
            _v("trailing_content", "unexpected line after a complete hunk")
        m = HUNK_HEADER.match(line)
        if not m:
            _v("malformed_hunk_header", "hunk header does not match '@@ -A[,B] +C[,D] @@'")
        if len(hunks) >= limits.max_hunks:
            _v("too_many_hunks", f"more than {limits.max_hunks} hunks")
        old_start, new_start = int(m.group(1)), int(m.group(3))
        old_count = int(m.group(2)) if m.group(2) is not None else 1
        new_count = int(m.group(4)) if m.group(4) is not None else 1
        i += 1
        body, old_seen, new_seen = [], 0, 0
        while old_seen < old_count or new_seen < new_count:
            if i >= len(lines):
                _v("truncated_hunk", "hunk ends before its header counts are satisfied")
            raw = lines[i]
            op, text = raw[:1], raw[1:]
            if op == "\\":
                _v("no_newline_marker_not_supported", "'\\ No newline at end of file' is not supported")
            if op not in (" ", "-", "+"):
                _v("malformed_hunk_line", "hunk line must start with ' ', '-' or '+'")
            if op in (" ", "-"):
                old_seen += 1
            if op in (" ", "+"):
                new_seen += 1
            if old_seen > old_count or new_seen > new_count:
                _v("hunk_count_mismatch", "hunk body does not match its header counts")
            body.append((op, text))
            i += 1
        if not any(op != " " for op, _ in body):
            _v("no_op_hunk", "hunk contains no added or removed lines")
        if i < len(lines) and lines[i][:1] == "\\":
            _v("no_newline_marker_not_supported", "'\\ No newline at end of file' is not supported")
        hunks.append(Hunk(old_start, old_count, new_start, new_count, tuple(body)))

    added = sum(1 for h in hunks for op, _ in h.lines if op == "+")
    removed = sum(1 for h in hunks for op, _ in h.lines if op == "-")
    if added > limits.max_added_lines:
        _v("too_many_added_lines", f"{added} > {limits.max_added_lines}")
    if removed > limits.max_removed_lines:
        _v("too_many_removed_lines", f"{removed} > {limits.max_removed_lines}")
    if added + removed > limits.max_changed_lines:
        _v("too_many_changed_lines", f"{added + removed} > {limits.max_changed_lines}")
    return tuple(hunks)


def apply_exact(base: bytes, hunks: tuple) -> bytes:
    """Apply hunks at exactly their stated positions. Returns the new file bytes."""
    base_lines = base.decode("utf-8").split("\n")[:-1]  # base verified: LF, ends with newline
    out, cursor, delta = [], 0, 0
    for h in hunks:
        start = h.old_start - 1 if h.old_count > 0 else h.old_start
        if start < 0 or start + h.old_count > len(base_lines) or (h.old_count > 0 and h.old_start < 1):
            _v("hunk_out_of_range", "hunk refers to lines outside the base file")
        if start < cursor:
            _v("hunk_order_or_overlap", "hunks must be ascending and non-overlapping")
        old_block = [t for op, t in h.lines if op != "+"]
        if base_lines[start:start + h.old_count] != old_block:
            found = any(base_lines[j:j + len(old_block)] == old_block
                        for j in range(0, len(base_lines) - len(old_block) + 1)) if old_block else False
            if found:
                _v("hunk_offset", "hunk context matches the base only at a different line (offsets refused)")
            _v("context_mismatch", "hunk context or removed lines do not match the base file")
        expected_new = start + delta + 1 if h.new_count > 0 else start + delta
        if h.new_start != expected_new:
            _v("hunk_offset", "new-file start line is inconsistent with the base position")
        out.extend(base_lines[cursor:start])
        out.extend(t for op, t in h.lines if op != "-")
        cursor = start + h.old_count
        delta += h.new_count - h.old_count
    out.extend(base_lines[cursor:])
    if not out:
        _v("file_deletion_not_allowed", "patch would empty the file")
    new = ("\n".join(out) + "\n").encode("utf-8")
    if new == base:
        _v("no_change", "patch does not change the file")
    return new


def validate(diff: str, path: str, base: bytes, limits) -> ValidatedPatch:
    hunks = parse(diff, path, limits)
    new = apply_exact(base, hunks)
    raw = diff.encode("utf-8")
    return ValidatedPatch(
        path=path, hunks=hunks,
        added=sum(1 for h in hunks for op, _ in h.lines if op == "+"),
        removed=sum(1 for h in hunks for op, _ in h.lines if op == "-"),
        patch_bytes=len(raw), patch_sha256=hashlib.sha256(raw).hexdigest(),
        new_content=new, new_content_sha256=hashlib.sha256(new).hexdigest())
