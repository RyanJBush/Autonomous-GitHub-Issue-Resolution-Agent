"""Paid-call approval packet (Codex M3 amendment 2).

`prepare` writes a packet that binds the exact request, prompt, base, model and parameters, cost estimate, spending
limit, code commit and workflow revision, with `operator_approval.approved: false`. Only the operator may change that
field. `verify` recomputes every binding and refuses unless all match and the operator's approval is present.
A matching digest is a consistency check; the operator's recorded approval is the authorization.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path

from . import caps, pricing, prompt
from .base import BaseVerificationError, verify as verify_base
from .demo_repo import PROJECT_ROOT, load_pinned
from .gitcmd import GitError, run_git
from .live_adapter import ADAPTER_VERSION
from .models import load_request

SCHEMA = "ira.live_approval.v1"
LABEL = ("PAID MODEL CALL APPROVAL REQUEST: one Messages API request under a supervised local procedure; "
         "does not authorize executing the response")
PROCEDURE_PATH = "docs/LIVE_CALL_PROCEDURE.md"
PROPOSAL_PATH = "demo/live/approval.json"  # the committed, unapproved proposal; the only file allowed to change after
SDK_PIN = "anthropic==2.8.0"
_APPROVAL_ID = re.compile(r"^[0-9a-f]{32}$")
# Fail closed on anything not committed: modified, staged, untracked and ignored files (venv, outputs, caches).
STRICT_STATUS = ("status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching")


class ApprovalError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _git(*args, check=True):
    env = {"GIT_DIR": str(PROJECT_ROOT / ".git"), "GIT_WORK_TREE": str(PROJECT_ROOT)}
    return run_git(list(args), cwd=PROJECT_ROOT, env_extra=env, check=check)


def _bindings(request_path: Path) -> dict:
    raw = Path(request_path).read_bytes()
    req = load_request(raw)
    if req.adapter.kind != "live_model":
        raise ApprovalError("request_is_not_live")
    pinned = load_pinned()
    try:
        vb = verify_base(req.repository.path, pinned, req.repository.model_dump())
    except BaseVerificationError as error:
        raise ApprovalError(f"base_verification_failed:{error.code}") from None
    text = prompt.build_prompt(base_commit=vb.base_commit, path=vb.allowlisted_file, issue_title=req.issue.title,
                               issue_body=req.issue.body, source=vb.source_bytes, limits=req.limits.model_dump())
    pb = text.encode("utf-8")
    a = req.adapter
    est = pricing.estimate_call(a.model, len(pb), a.max_tokens)
    if not pricing.within_cap(est, a.cost_cap_cents):
        raise ApprovalError("estimate_exceeds_request_cap")
    proc = PROJECT_ROOT / PROCEDURE_PATH
    return {
        "request_path": str(Path(request_path).resolve().relative_to(PROJECT_ROOT))
        if Path(request_path).resolve().is_relative_to(PROJECT_ROOT) else str(request_path),
        "request_sha256": hashlib.sha256(raw).hexdigest(),
        "run_id": req.run_id,
        "prompt_sha256": hashlib.sha256(pb).hexdigest(), "prompt_bytes": len(pb),
        "prompt_template_version": prompt.TEMPLATE_VERSION, "prompt_template_sha256": prompt.TEMPLATE_SHA256,
        "base_commit": vb.base_commit, "allowlisted_file": vb.allowlisted_file,
        "source_sha256": hashlib.sha256(vb.source_bytes).hexdigest(),
        "provider": "Anthropic Messages API (api.anthropic.com)",
        "model": a.model, "authorized_response_models": list(a.authorized_response_models),
        "max_tokens": a.max_tokens, "effort": a.effort, "timeout_s": a.timeout_s, "thinking": "model default (adaptive)",
        "tools": None, "system_prompt": None, "fallbacks": None, "max_retries": 0, "requests": 1,
        "cost_cap_usd": f"{a.cost_cap_cents / 100:.2f}", "estimate": est,
        "adapter_version": ADAPTER_VERSION, "sdk": SDK_PIN,
        "procedure_path": PROCEDURE_PATH,
        "procedure_sha256": hashlib.sha256(proc.read_bytes()).hexdigest() if proc.is_file() else None,
    }


def prepare(request_path: Path, out_path: Path, *, console_spend_limit_usd: str = "1.00") -> dict:
    out_path = Path(out_path)
    if out_path.exists():
        raise ApprovalError("approval_packet_exists")
    try:
        commit = _git("rev-parse", "--verify", "HEAD^{commit}").stdout.decode().strip()
        dirty = bool(_git(*STRICT_STATUS).stdout)
    except GitError:
        raise ApprovalError("project_commit_unavailable") from None
    if dirty:
        raise ApprovalError("project_tree_dirty")
    packet = {
        "schema_version": SCHEMA, "label": LABEL, "status": "awaiting_operator_approval",
        "approval_id": secrets.token_hex(16), "code_commit": commit, "binds": _bindings(request_path),
        "single_use": {
            "mechanism": "local ledger: an atomic exclusive marker named after approval_id, created outside the "
                         "checkout immediately before the request is sent",
            "scope": "this operator machine and ledger directory only; NOT enforced globally",
            "procedure": "one operator, one machine, one attempt. Any second attempt, including after a timeout, "
                         "crash or otherwise ambiguous outcome, needs a newly prepared packet (new approval_id) and "
                         "fresh explicit operator approval.",
        },
        "spending_limit": {
            "per_request_cap_usd": None, "recommended_console_workspace_spend_limit_usd": console_spend_limit_usd,
            "note": "Set an independent workspace spend limit in the Claude Console before approving "
                    "(platform.claude.com/settings/spend-limits); this code cannot verify that it is set.",
            "source": pricing.SOURCES["spend_limits"]},
        "what_approval_authorizes": [
            "one Messages API request with the bound parameters, from the bound commit, following the bound procedure",
            "recording the bounded response and writing a review packet",
        ],
        "what_approval_does_not_authorize": [
            "executing the response or any code derived from it",
            "retries or additional requests",
            "creating a pull request or merging anything",
        ],
        "not_verified_before_approval": [
            "account access, credits and the model's availability to this account",
            "actual charges: the estimate and the request cap are not provider-side limits",
            "that the Console spend limit is set",
        ],
        "operator_approval": {"approved": False, "approved_by": None, "approved_at": None, "statement": None},
    }
    packet["spending_limit"]["per_request_cap_usd"] = packet["binds"]["cost_cap_usd"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n")
    return packet


def verify(packet_path: Path, request_path: Path) -> dict:
    """Return the verified packet, or raise ApprovalError. Makes no network request."""
    try:
        packet = json.loads(Path(packet_path).read_text())
    except (OSError, ValueError):
        raise ApprovalError("approval_packet_unreadable") from None
    if packet.get("schema_version") != SCHEMA or packet.get("label") != LABEL \
            or not _APPROVAL_ID.match(str(packet.get("approval_id") or "")):
        raise ApprovalError("approval_packet_malformed")
    approval = packet.get("operator_approval") or {}
    if approval.get("approved") is not True or not approval.get("approved_by") or not approval.get("approved_at"):
        raise ApprovalError("operator_approval_missing")
    current = _bindings(request_path)
    for key, value in current.items():
        if packet.get("binds", {}).get(key) != value:
            raise ApprovalError(f"binding_mismatch:{key}")
    try:
        head = _git("rev-parse", "--verify", "HEAD^{commit}").stdout.decode().strip()
        if head != packet.get("code_commit"):
            # Allowed only if HEAD descends from the bound commit and changes nothing but this approval packet.
            if _git("merge-base", "--is-ancestor", str(packet["code_commit"]), head, check=False).returncode != 0:
                raise ApprovalError("code_commit_not_ancestor")
            changed = set(filter(None, _git("diff", "--name-only", packet["code_commit"], head).stdout.decode()
                                 .splitlines()))
            if changed - {PROPOSAL_PATH}:
                raise ApprovalError("code_changed_since_approval")
        if _git(*STRICT_STATUS).stdout:
            raise ApprovalError("project_tree_dirty")
    except GitError:
        raise ApprovalError("code_commit_unverifiable") from None
    return packet
