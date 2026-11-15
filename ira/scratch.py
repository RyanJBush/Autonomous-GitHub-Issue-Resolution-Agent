"""Scratch application and confirmation of an already-validated patch (M1 amendment 4).

A fresh repository containing only the pinned base copy of the allowlisted file is created under a scratch
directory. `git apply` receives the diff on stdin. The result must agree with policy.apply_exact byte for byte, and
git's own numstat/raw/status views must show exactly one modified regular file with the policy's line counts.
`--unidiff-zero` lets git accept zero-context hunks; positions were already verified exactly by policy.apply_exact,
and the byte-for-byte content comparison below confirms git applied them at the same place.
This is a controlled file operation, not a sandbox; no file in the scratch tree is executed or imported.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .gitcmd import GitError, run_git
from .policy import ValidatedPatch


class ApplyFailed(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class ApplyConfirmation:
    numstat: tuple  # (added, removed, path)
    raw_status: str
    git_content_sha256: str
    matches_policy_content: bool


def _read_regular(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ApplyFailed("not_regular_file", "applied path is not a regular file")
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def apply_and_confirm(base_bytes: bytes, patch: ValidatedPatch, diff: str, work_root: Path) -> ApplyConfirmation:
    work_root = Path(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="ira-scratch-", dir=work_root))
    env = {"GIT_DIR": str(scratch / ".git"), "GIT_WORK_TREE": str(scratch)}

    def git(*args, stdin=None, check=True):
        return run_git(list(args), cwd=scratch, stdin=stdin, env_extra=env, check=check)

    try:
        git("init", "--quiet", "--template=", str(scratch))
        target = scratch / patch.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base_bytes)
        os.chmod(target, 0o644)
        git("add", "--", patch.path)
        diff_bytes = diff.encode("utf-8")
        if git("apply", "--check", "--unidiff-zero", "--whitespace=nowarn", "-p1", "-", stdin=diff_bytes, check=False).returncode:
            raise ApplyFailed("git_apply_check_failed", "git apply --check rejected a policy-validated patch")
        if git("apply", "--unidiff-zero", "--whitespace=nowarn", "-p1", "-", stdin=diff_bytes, check=False).returncode:
            raise ApplyFailed("git_apply_failed", "git apply failed on a policy-validated patch")

        entries = [e for e in git("diff", "--numstat", "-z", "--no-renames", "--").stdout.split(b"\0") if e]
        if len(entries) != 1:
            raise ApplyFailed("confirm_numstat", "numstat does not list exactly one file")
        parts = entries[0].decode("utf-8", "replace").split("\t")
        if len(parts) != 3 or parts[2] != patch.path or parts[:2] != [str(patch.added), str(patch.removed)]:
            raise ApplyFailed("confirm_numstat", "numstat path or counts differ from policy")

        raw = [e for e in git("diff", "--raw", "-z", "--no-renames", "--no-abbrev", "--").stdout.split(b"\0") if e]
        if len(raw) != 2:
            raise ApplyFailed("confirm_raw", "raw diff does not list exactly one file")
        meta = raw[0].decode("ascii", "replace").split(" ")
        if len(meta) != 5 or meta[0] != ":100644" or meta[1] != "100644" or meta[4] != "M" \
                or raw[1].decode("utf-8", "replace") != patch.path:
            raise ApplyFailed("confirm_raw", "raw diff is not a single in-place modification of a regular file")

        status = git("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
        # Unborn scratch repository: the base copy is staged (A) and the patched work tree differs from it (M).
        if status != f"AM {patch.path}\0".encode():
            raise ApplyFailed("confirm_status", "scratch tree has changes other than the allowlisted file")

        content = _read_regular(target)
        if content != patch.new_content:
            raise ApplyFailed("confirm_content", "git apply result differs from policy-computed content")
        return ApplyConfirmation((patch.added, patch.removed, patch.path), "M",
                                 hashlib.sha256(content).hexdigest(), True)
    except GitError as error:
        raise ApplyFailed("git_error", str(error)) from None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
