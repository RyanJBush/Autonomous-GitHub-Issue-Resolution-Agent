"""`ira check-operator-env`: verify the operator's machine before any approval is requested.

No API key, no network request, no model call. It checks only what the supervised procedure needs locally.
"""
from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

from . import approval
from .base import BaseVerificationError, verify as verify_base
from .demo_repo import PROJECT_ROOT, load_pinned
from .gitcmd import GitError, git_executable
from .models import RequestError, load_request
from .single_use import LedgerError, SingleUseLedger, default_ledger_dir

REQUIRED_SDK = "2.8.0"
CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def run_checks(request_path: str, ledger_dir: str | None = None) -> list[dict]:
    rows = []

    def add(name, ok, detail=""):
        rows.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})

    add("linux", sys.platform == "linux", f"{platform.system()} {platform.release()}")
    add("python_3_12_or_newer", sys.version_info >= (3, 12), platform.python_version())
    try:
        add("git_on_trusted_path", True, git_executable())
    except GitError as error:
        add("git_on_trusted_path", False, error)
    try:
        import anthropic
        add("anthropic_sdk_pinned", anthropic.__version__ == REQUIRED_SDK, anthropic.__version__)
    except ImportError:
        add("anthropic_sdk_pinned", False, "anthropic not installed")
    add("venv_outside_checkout", not Path(sys.prefix).resolve().is_relative_to(PROJECT_ROOT), "sys.prefix checked")
    add("no_bytecode_writes", sys.flags.dont_write_bytecode, "run with PYTHONDONTWRITEBYTECODE=1")
    try:
        status = approval._git(*approval.STRICT_STATUS).stdout
        add("checkout_strictly_clean", not status, f"{len(status.splitlines())} status entries")
        head = approval._git("rev-parse", "--verify", "HEAD^{commit}").stdout.decode().strip()
        add("checkout_head", True, head)
    except GitError as error:
        add("checkout_strictly_clean", False, error)
    proposal_id = None
    proposal = PROJECT_ROOT / approval.PROPOSAL_PATH
    try:
        pkt = json.loads(proposal.read_text())
        proposal_id = pkt.get("approval_id")
        add("committed_proposal_unapproved", pkt["operator_approval"]["approved"] is False, proposal_id)
    except (OSError, ValueError, KeyError):
        add("committed_proposal_unapproved", False, "proposal packet missing or unreadable")
    try:
        req = load_request(Path(request_path).read_bytes())
        vb = verify_base(req.repository.path, load_pinned(), req.repository.model_dump())
        add("demo_repository_verified", True, vb.base_commit)
    except (OSError, RequestError, BaseVerificationError) as error:
        add("demo_repository_verified", False, getattr(error, "code", type(error).__name__))
    try:
        ledger = SingleUseLedger(ledger_dir or default_ledger_dir())
        add("ledger_outside_checkout_writable", os.access(ledger.directory, os.W_OK), "directory ready")
        if proposal_id:
            add("proposal_approval_unused", not ledger.is_used(proposal_id), proposal_id)
    except LedgerError as error:
        add("ledger_outside_checkout_writable", False, error)
    unset = [v for v in CREDENTIAL_VARS if os.environ.get(v)]
    add("no_credential_environment_variables", not unset, ",".join(unset) or "none set")
    add("interactive_terminal", sys.stdin.isatty(), "needed for the hidden key prompt")
    return rows
