"""Build the explicitly synthetic demonstration repository with a reproducible commit SHA.

Same input bytes + fixed author, committer, dates, message and file modes => same commit id on every machine.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .gitcmd import run_git

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET_DIR = PROJECT_ROOT / "demo" / "target"
PINNED_PATH = PROJECT_ROOT / "demo" / "pinned.json"

IDENTITY = "ira-synthetic-demo-v1"
COMMIT_MESSAGE = "SYNTHETIC demonstration repository for issue-resolution-agent (not a real project)\n"
_FIXED = {
    "GIT_AUTHOR_NAME": "Synthetic Demo",
    "GIT_AUTHOR_EMAIL": "synthetic-demo@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000",
    "GIT_COMMITTER_NAME": "Synthetic Demo",
    "GIT_COMMITTER_EMAIL": "synthetic-demo@example.invalid",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
}


def target_files() -> list[tuple[str, bytes]]:
    files = []
    for path in sorted(TARGET_DIR.rglob("*")):
        if path.is_symlink():
            raise ValueError("demo target must not contain symlinks")
        if path.is_file():
            files.append((path.relative_to(TARGET_DIR).as_posix(), path.read_bytes()))
    return files


def build(dest: Path | str) -> str:
    """Create the demo repository at `dest` (must not exist). Returns the commit id."""
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(f"destination already exists: {dest}")
    dest.mkdir(parents=True)
    run_git(["init", "--quiet", "--template=", str(dest)], cwd=dest)
    for rel, content in target_files():
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
        os.chmod(out, 0o644)
    env = {"GIT_DIR": str(dest / ".git"), "GIT_WORK_TREE": str(dest), **_FIXED}
    run_git(["add", "--all", "--", "."], cwd=dest, env_extra=env)
    run_git(["commit", "--quiet", "--no-verify", "-m", COMMIT_MESSAGE.strip()], cwd=dest, env_extra=env)
    return run_git(["rev-parse", "--verify", "HEAD^{commit}"], cwd=dest, env_extra=env).stdout.decode().strip()


def load_pinned() -> dict:
    return json.loads(PINNED_PATH.read_text(encoding="utf-8"))
