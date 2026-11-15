"""Local single-use ledger for an approved live call (Codex M3 review, finding 2).

Scope, stated plainly: this prevents a second use of one approval *on the machine and ledger directory where it is
claimed*. It is not global: another machine, a deleted ledger, or a different ledger directory is not covered. The
supervised procedure (docs/LIVE_CALL_PROCEDURE.md) covers the rest: one operator, one machine, one attempt, and fresh
explicit approval (a new approval id) for any second attempt, including after a timeout or other ambiguous outcome.

The claim is an atomic exclusive create (O_CREAT | O_EXCL) made immediately before the request is sent, so concurrent
or repeated attempts cannot both proceed, and an attempt that times out or crashes has still consumed the approval.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .demo_repo import PROJECT_ROOT

_ID = re.compile(r"^[0-9a-f]{32}$")


class AlreadyUsed(Exception):
    pass


class LedgerError(Exception):
    pass


def default_ledger_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "ira" / "live-approvals"


class SingleUseLedger:
    def __init__(self, directory: Path | str):
        directory = Path(directory).resolve()
        if directory.is_relative_to(PROJECT_ROOT):
            raise LedgerError("the single-use ledger must be outside the project checkout")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink() or not directory.is_dir():
            raise LedgerError("ledger path must be a real directory")
        self.directory = directory

    def marker(self, approval_id: str) -> Path:
        if not _ID.match(approval_id or ""):
            raise LedgerError("approval id must be 32 lowercase hex characters")
        return self.directory / f"{approval_id}.spent"

    def is_used(self, approval_id: str) -> bool:
        return self.marker(approval_id).exists()

    def claim(self, approval_id: str, *, request_sha256: str, run_id: str) -> str:
        """Atomically consume the approval. Raises AlreadyUsed if it was claimed before (by any process)."""
        path = self.marker(approval_id)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except FileExistsError:
            raise AlreadyUsed("this approval has already been used on this machine") from None
        try:
            body = {"approval_id": approval_id, "request_sha256": request_sha256, "run_id": run_id,
                    "claimed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "meaning": "claimed immediately before sending; any outcome, including a timeout, consumes it"}
            os.write(fd, (json.dumps(body, sort_keys=True) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        dfd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        return path.name
