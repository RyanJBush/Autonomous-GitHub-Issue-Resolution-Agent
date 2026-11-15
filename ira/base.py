"""Independent verification of the demonstration repository checkout (contract item 3, M1 amendment 1).

Order matters: filesystem layout and the repository's own config file are checked first, by reading them as data,
before any git command that would honour that config (for example `git status` runs a configured fsmonitor).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import caps
from .gitcmd import GitError, run_git

_HEX40 = re.compile(r"^[0-9a-f]{40}$")

# Keys `git init` writes on supported platforms. Anything else (fsmonitor, filters, hooksPath, include.path,
# alias, remote, extensions, ...) fails verification.
ALLOWED_LOCAL_CONFIG = {
    "core.repositoryformatversion": {"0"},
    "core.filemode": {"true", "false"},
    "core.bare": {"false"},
    "core.logallrefupdates": {"true", "false"},
    "core.ignorecase": {"true", "false"},
    "core.precomposeunicode": {"true", "false"},
    "core.symlinks": {"true", "false"},
}
FORBIDDEN_GIT_PATHS = ("commondir", "objects/info/alternates", "info/grafts", "shallow")


class BaseVerificationError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class VerifiedBase:
    path: str
    identity: str
    base_commit: str
    base_tree: str
    allowlisted_file: str
    allowlisted_blob: str
    source_bytes: bytes
    checks: tuple  # ((check, "pass"), ...)


def _fail(code, detail):
    raise BaseVerificationError(code, detail)


def verify(repo_path: Path | str, pinned: dict, request_repo: dict) -> VerifiedBase:
    checks = []

    def ok(name):
        checks.append((name, "pass"))

    for key, pinned_key in (("identity", "identity"), ("base_commit", "base_commit"),
                            ("allowlisted_file", "allowlisted_file")):
        if request_repo.get(key) != pinned.get(pinned_key):
            _fail("request_not_pinned", f"request {key} does not equal the trusted pinned value")
    ok("request_matches_pinned")
    base, tree, path = pinned["base_commit"], pinned["base_tree"], pinned["allowlisted_file"]
    if not (_HEX40.match(base) and _HEX40.match(tree) and _HEX40.match(pinned["allowlisted_blob"])):
        _fail("pinned_invalid", "pinned ids are not 40-hex")

    root = Path(repo_path)
    if root.is_symlink() or not root.is_dir():
        _fail("repo_not_directory", "repository path is a symlink or not a directory")
    root = root.resolve(strict=True)
    git_dir = root / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        _fail("git_dir_invalid", ".git must be a real directory (no gitfile, worktree or symlink)")
    for rel in FORBIDDEN_GIT_PATHS:
        if (git_dir / rel).exists() or (git_dir / rel).is_symlink():
            _fail("git_dir_invalid", f".git/{rel} is not allowed")
    config_file = git_dir / "config"
    if config_file.is_symlink() or not config_file.is_file():
        _fail("git_config_invalid", ".git/config must be a regular file")
    ok("layout")

    try:
        raw = run_git(["config", "--no-includes", "--file", str(config_file), "--list", "-z"], cwd=root).stdout
    except GitError as error:
        _fail("git_config_invalid", str(error))
    for entry in filter(None, raw.split(b"\0")):
        key, _, value = entry.decode("utf-8", "replace").partition("\n")
        if key.lower() not in ALLOWED_LOCAL_CONFIG or value.lower() not in ALLOWED_LOCAL_CONFIG[key.lower()]:
            _fail("git_config_not_allowed", f"local config key not allowed: {key[:80]}")
    ok("local_config_allowlist")

    env = {"GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(root)}

    def git(*args):
        try:
            return run_git(list(args), cwd=root, env_extra=env).stdout
        except GitError as error:
            _fail("git_failed", str(error))

    head = git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if head != base:
        _fail("head_mismatch", "HEAD is not the pinned base commit")
    ok("head_is_pinned_commit")
    if git("rev-parse", "--verify", base + "^{tree}").decode().strip() != tree:
        _fail("tree_mismatch", "base commit tree differs from pinned tree")
    ok("tree_is_pinned_tree")

    entries = [e for e in git("ls-tree", "-z", base, "--", path).split(b"\0") if e]
    if len(entries) != 1:
        _fail("allowlisted_file_missing", "allowlisted file is not exactly one tree entry")
    meta, _, name = entries[0].decode("utf-8", "replace").partition("\t")
    mode, kind, blob = (meta.split(" ") + ["", "", ""])[:3]
    if name != path or kind != "blob":
        _fail("allowlisted_file_missing", "allowlisted entry is not a blob at the exact path")
    if mode != "100644":
        _fail("allowlisted_file_mode", f"allowlisted file mode {mode} is not 100644 (symlink/executable refused)")
    if blob != pinned["allowlisted_blob"]:
        _fail("blob_mismatch", "allowlisted blob differs from pinned blob")
    ok("allowlisted_file_regular_and_pinned")

    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching")
    if status:
        _fail("dirty_tree", "working tree has modified, staged, untracked or ignored files")
    ok("clean_tree_including_untracked_and_ignored")

    source = git("cat-file", "blob", blob)
    if len(source) > caps.MAX_SOURCE_FILE_BYTES:
        _fail("source_too_large", "allowlisted file exceeds MAX_SOURCE_FILE_BYTES")
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        _fail("source_not_supported", "allowlisted file is not UTF-8")
    if "\r" in text or "\0" in text or not text.endswith("\n"):
        _fail("source_not_supported", "allowlisted file must use LF, contain no NUL and end with a newline")
    ok("source_file_supported")

    return VerifiedBase(str(root), pinned["identity"], base, tree, path, blob, source, tuple(checks))
