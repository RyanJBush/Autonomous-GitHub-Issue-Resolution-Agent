"""The only place this package starts a subprocess: trusted git with controlled configuration.

Controls (observed hazards on the development host: /etc/gitconfig defines git-lfs filter drivers, ~/.gitconfig
enables commit signing through an external program, and the proxy injects config through GIT_CONFIG_KEY_* env vars):

* The environment is built from scratch; nothing is inherited, so credentials and GIT_CONFIG_* injection never
  reach git. System and global config are disabled (GIT_CONFIG_NOSYSTEM, GIT_CONFIG_GLOBAL=/dev/null).
* Every call carries -c overrides that disable hooks, fsmonitor, signing, pagers, auto-gc and all transports.
* Replace refs are ignored, so an object id always means its real content.
* Subcommands are limited to a fixed set. Untrusted text (a candidate diff) may only be passed on stdin.

This is configuration control for trusted file operations. It is not a sandbox.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import caps

ALLOWED_SUBCOMMANDS = frozenset({
    "init", "add", "commit", "rev-parse", "cat-file", "ls-tree", "status", "config", "apply", "diff",
})

# Subcommands the candidate-handling path (everything after the model response is received) may use.
CANDIDATE_PATH_SUBCOMMANDS = frozenset({"init", "add", "apply", "diff", "status"})

CONFIG_OVERRIDES = (
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "core.untrackedCache=false",
    "core.autocrlf=false",
    "core.safecrlf=false",
    "core.pager=cat",
    "core.quotePath=true",
    "commit.gpgsign=false",
    "tag.gpgsign=false",
    "gc.auto=0",
    "maintenance.auto=false",
    "protocol.allow=never",
    "advice.detachedHead=false",
    "init.defaultBranch=main",
)

MAX_OUTPUT_BYTES = 1_048_576
_SAFE_PATH = "/usr/bin:/bin"


class GitError(RuntimeError):
    """A trusted git operation failed. The message never contains candidate text."""


@dataclass(frozen=True)
class GitResult:
    args: tuple
    returncode: int
    stdout: bytes
    stderr: bytes


def git_executable() -> str:
    found = shutil.which("git", path=_SAFE_PATH)
    if not found:
        raise GitError("git not found on the trusted PATH")
    return found


def controlled_env(extra: dict | None = None) -> dict:
    env = {
        "PATH": _SAFE_PATH,
        "HOME": "/nonexistent",
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "0",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    if extra:
        env.update(extra)
    return env


def run_git(args, *, cwd: Path | str, stdin: bytes | None = None, check: bool = True,
            env_extra: dict | None = None) -> GitResult:
    args = tuple(args)
    if not args or args[0] not in ALLOWED_SUBCOMMANDS:
        raise GitError(f"git subcommand not allowed: {args[:1]!r}")
    for a in args:
        if type(a) is not str or "\0" in a or "\n" in a or len(a) > 4096:
            raise GitError("git argument rejected")
    argv = [git_executable()]
    for override in CONFIG_OVERRIDES:
        argv += ["-c", override]
    argv += list(args)
    try:
        proc = subprocess.run(argv, cwd=str(cwd), input=stdin if stdin is not None else b"",
                              capture_output=True, env=controlled_env(env_extra),
                              timeout=caps.GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(f"git {args[0]} could not run: {type(error).__name__}") from error
    if len(proc.stdout) > MAX_OUTPUT_BYTES or len(proc.stderr) > MAX_OUTPUT_BYTES:
        raise GitError(f"git {args[0]} output exceeded {MAX_OUTPUT_BYTES} bytes")
    result = GitResult(args, proc.returncode, proc.stdout, proc.stderr)
    if check and proc.returncode != 0:
        raise GitError(f"git {args[0]} exited {proc.returncode}")
    return result
