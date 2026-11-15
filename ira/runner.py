"""One bounded run: request -> base verification -> one proposal -> schema -> policy -> scratch confirm -> gate.

Milestone 1 never executes candidate code: the execution gate always refuses, so an accepted patch ends at
blocked_boundary_unverified.
"""
from __future__ import annotations

import hashlib
import platform
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, boundary, caps, packet, policy, prompt
from .adapters import AdapterError, FixtureAdapter
from .base import BaseVerificationError, verify
from .demo_repo import PROJECT_ROOT, load_pinned
from .gitcmd import GitError, run_git
from .models import CannotPropose, RequestError, RunRequest, SchemaError, load_request, parse_response
from .sanitize import bounded
from .scratch import ApplyFailed, apply_and_confirm
from .status import Status

LIMITATIONS_M1 = [
    "Milestone 1: no candidate code is executed; trusted tests are not run.",
    "No live model adapter exists in Milestone 1; fixture runs are synthetic and prove nothing about model quality.",
    "Scratch application is a controlled git file operation, not a sandbox.",
    "No execution boundary (sandbox) is verified by Milestone 1.",
    "A future passing test is evidence for human review only; it authorizes no PR or merge.",
]


def default_adapter_factory(spec):
    return FixtureAdapter(spec.fixture_case)


class _Run:
    def __init__(self, req: RunRequest, raw_request: bytes):
        self.req = req
        self.started = time.monotonic()
        self.trace = []
        self.decisions = []
        self.unavailable = {}
        self.stage = "request"
        self.adapter_identity = {"kind": req.adapter.kind, "name": None, "model": None, "calls": 0}
        self.fields = {k: None for k in ("base", "prompt", "response", "parsed_response", "patch", "apply")}
        self.request_sha256 = hashlib.sha256(raw_request).hexdigest()

    def event(self, stage, name, code=None):
        self.stage = stage
        self.trace.append({"seq": len(self.trace) + 1, "stage": stage, "event": name, "code": code,
                           "t_ms": round((time.monotonic() - self.started) * 1000, 3)})

    def decide(self, check, result, code=None, detail=None):
        self.decisions.append({"check": check, "result": result, "code": code,
                               "detail": bounded(detail, 300)[0] if detail else None})


def _project_commit():
    env = {"GIT_DIR": str(PROJECT_ROOT / ".git"), "GIT_WORK_TREE": str(PROJECT_ROOT)}
    try:
        commit = run_git(["rev-parse", "--verify", "HEAD^{commit}"], cwd=PROJECT_ROOT, env_extra=env).stdout
        dirty = run_git(["status", "--porcelain=v1", "--untracked-files=normal"], cwd=PROJECT_ROOT, env_extra=env).stdout
        # True when the code that produced this packet may differ from `commit` (modified or untracked files).
        return {"commit": commit.decode().strip(), "working_tree_dirty": bool(dirty)}, None
    except (GitError, OSError) as error:
        return None, f"project commit unavailable: {type(error).__name__}"


def execute(request_path: Path | str, out_dir: Path | str, *, adapter_factory=default_adapter_factory,
            work_root: Path | str | None = None, pinned: dict | None = None) -> tuple[dict, Path]:
    raw_request = Path(request_path).read_bytes()
    req = load_request(raw_request)  # RequestError propagates: no run, no packet
    run_dir = Path(out_dir) / req.run_id
    if run_dir.exists():
        raise RequestError("run_id already has a packet: one attempt per run id")
    pinned = pinned or load_pinned()
    r = _Run(req, raw_request)
    status, reason = Status.EXECUTION_ERROR, "internal_error"
    try:
        status, reason = _pipeline(r, pinned, adapter_factory, Path(work_root or tempfile.gettempdir()))
    except Exception as error:  # trusted-code failure; never candidate execution in M1
        r.event(r.stage, "internal_error", type(error).__name__)
        status, reason = Status.EXECUTION_ERROR, f"internal_error:{type(error).__name__}"
    pkt = _assemble(r, pinned, status, reason)
    problems = packet.check_unavailable_invariant(pkt)
    if problems:
        raise RuntimeError("packet invariant violated: " + "; ".join(problems))
    return pkt, packet.write(run_dir, pkt)


def _pipeline(r: _Run, pinned: dict, adapter_factory, work_root: Path):
    req = r.req
    r.event("request", "request_loaded")

    # Trusted test identity must be the pinned one, and the file on disk must match its hash.
    tt, pt = req.trusted_test, pinned["trusted_test"]
    test_file = PROJECT_ROOT / pt["path"]
    on_disk = hashlib.sha256(test_file.read_bytes()).hexdigest() if test_file.is_file() else None
    if tt.path != pt["path"] or tt.sha256 != pt["sha256"] or list(tt.test_ids) != pt["test_ids"] or on_disk != pt["sha256"]:
        r.decide("trusted_test_identity", "fail", "trusted_test_not_pinned")
        r.event("request", "rejected", "trusted_test_not_pinned")
        _unavail_from(r, "base", "trusted test identity check failed before base verification")
        return Status.REJECTED_POLICY, "trusted_test_not_pinned"
    r.decide("trusted_test_identity", "pass")

    # Base verification (independent of the model, before any model call).
    try:
        vb = verify(req.repository.path, pinned, req.repository.model_dump())
    except BaseVerificationError as error:
        r.decide("base_verification", "fail", error.code, error.detail)
        r.event("base_verification", "rejected", error.code)
        _unavail_from(r, "base", f"base verification failed: {error.code}")
        return Status.REJECTED_POLICY, error.code
    for check, result in vb.checks:
        r.decide(f"base.{check}", result)
    r.fields["base"] = {"identity": vb.identity, "path": vb.path, "base_commit": vb.base_commit,
                        "base_tree": vb.base_tree, "allowlisted_file": vb.allowlisted_file,
                        "allowlisted_blob": vb.allowlisted_blob,
                        "source_sha256": hashlib.sha256(vb.source_bytes).hexdigest(), "verified": True}
    r.event("base_verification", "verified")

    limits = req.limits.model_dump()
    text = prompt.build_prompt(base_commit=vb.base_commit, path=vb.allowlisted_file, issue_title=req.issue.title,
                               issue_body=req.issue.body, source=vb.source_bytes, limits=limits)
    pb = text.encode("utf-8")
    r.fields["prompt"] = {"template_version": prompt.TEMPLATE_VERSION, "template_sha256": prompt.TEMPLATE_SHA256,
                          "prompt_sha256": hashlib.sha256(pb).hexdigest(), "prompt_bytes": len(pb),
                          "inputs": list(prompt.PROMPT_INPUTS)}
    r.event("prompt", "built")

    # Exactly one adapter call.
    try:
        adapter = adapter_factory(req.adapter)
        r.adapter_identity = {**adapter.identity(), "calls": 0}
        raw = adapter.propose(text, req.limits.max_response_bytes)
        r.adapter_identity["calls"] = adapter.calls
        if type(raw) is not bytes:
            raise AdapterError("adapter_returned_non_bytes", "adapter must return bytes")
    except AdapterError as error:
        r.adapter_identity["calls"] = getattr(locals().get("adapter"), "calls", 0)
        r.event("adapter", "error", error.code)
        _unavail_from(r, "response", f"adapter error: {error.code}")
        return Status.ADAPTER_ERROR, error.code
    stored = raw[:caps.MAX_PACKET_RAW_RESPONSE_BYTES]
    r.fields["response"] = {"raw_sha256": hashlib.sha256(raw).hexdigest(), "raw_bytes": len(raw),
                            "stored_text": stored.decode("utf-8", errors="replace"),
                            "stored_bytes": len(stored), "truncated": len(raw) > len(stored),
                            "lossy_decode": _lossy(stored)}
    r.event("adapter", "response_received")

    try:
        parsed = parse_response(raw, req.limits.max_response_bytes)
    except SchemaError as error:
        r.decide("response_schema", "fail", error.code, str(error.errors) if error.errors else error.detail)
        r.event("schema", "rejected", error.code)
        _unavail_from(r, "parsed_response", f"response rejected by schema: {error.code}")
        return Status.REJECTED_SCHEMA, error.code
    r.decide("response_schema", "pass")
    r.fields["parsed_response"] = parsed.model_dump()
    r.event("schema", "valid", parsed.outcome)

    if isinstance(parsed, CannotPropose):
        _unavail_from(r, "patch", "model returned cannot_propose")
        return Status.CANNOT_PROPOSE, "model_cannot_propose"

    if parsed.base_commit != vb.base_commit:
        r.decide("base_commit_echo", "fail", "base_commit_mismatch")
        r.event("policy", "rejected", "base_commit_mismatch")
        _unavail_from(r, "patch", "rejected by policy: base_commit_mismatch")
        return Status.REJECTED_POLICY, "base_commit_mismatch"
    r.decide("base_commit_echo", "pass")

    try:
        vp = policy.validate(parsed.diff, vb.allowlisted_file, vb.source_bytes, req.limits)
    except policy.PolicyViolation as error:
        r.decide("patch_policy", "fail", error.code, error.detail)
        r.event("policy", "rejected", error.code)
        _unavail_from(r, "patch", f"rejected by policy: {error.code}")
        return Status.REJECTED_POLICY, error.code
    r.decide("patch_policy", "pass")
    r.fields["patch"] = {"path": vp.path, "sha256": vp.patch_sha256, "bytes": vp.patch_bytes, "hunks": len(vp.hunks),
                         "added": vp.added, "removed": vp.removed, "diff": parsed.diff,
                         "new_content_sha256": vp.new_content_sha256}
    r.event("policy", "accepted")

    try:
        conf = apply_and_confirm(vb.source_bytes, vp, parsed.diff, work_root)
    except ApplyFailed as error:
        r.decide("scratch_apply_confirm", "fail", error.code, error.detail)
        r.event("apply", "failed", error.code)
        _unavail_from(r, "apply", f"scratch apply failed: {error.code}")
        return Status.APPLY_FAILED, error.code
    r.decide("scratch_apply_confirm", "pass")
    r.fields["apply"] = {"numstat": list(conf.numstat), "raw_status": conf.raw_status,
                         "git_content_sha256": conf.git_content_sha256,
                         "matches_policy_content": conf.matches_policy_content,
                         "method": "fresh scratch repository with controlled git configuration (not a sandbox)"}
    r.event("apply", "confirmed")

    gate = boundary.verify_execution_boundary()
    r.decide("execution_boundary", "fail" if not gate.verified else "pass", gate.code)
    r.event("execution", "blocked", gate.code)
    return Status.BLOCKED_BOUNDARY_UNVERIFIED, gate.code


_ORDER = ("base", "prompt", "response", "parsed_response", "patch", "apply")


def _unavail_from(r: _Run, first: str, reason: str) -> None:
    """Mark `first` and every later pipeline field that is still unset as unavailable."""
    for name in _ORDER[_ORDER.index(first):]:
        if r.fields[name] is None:
            r.unavailable.setdefault(name, reason if name == first else f"not reached: {reason}")


def _lossy(b: bytes) -> bool:
    try:
        b.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def _assemble(r: _Run, pinned: dict, status: Status, reason: str) -> dict:
    req = r.req
    for name in _ORDER:
        if r.fields[name] is None and name not in r.unavailable:
            r.unavailable[name] = f"not reached (terminal status {status.value})"
    commit, commit_reason = _project_commit()
    unavailable = dict(r.unavailable)
    if commit is None:
        unavailable["provenance.project_commit"] = commit_reason
    not_run = ("Milestone 1: trusted tests are not executed (no verified execution boundary)"
               if status is Status.BLOCKED_BOUNDARY_UNVERIFIED else f"not run: terminal status {status.value}")
    unavailable["tests.baseline"] = not_run
    unavailable["tests.candidate"] = not_run
    kind = req.adapter.kind
    return {
        "schema_version": packet.SCHEMA_VERSION,
        "label": packet.RUN_KIND_LABELS[kind],
        "run_kind": kind,
        "run_id": req.run_id,
        "terminal_status": status.value,
        "status_reason": reason,
        "stage_reached": r.stage,
        "provenance": {"tool": "issue-resolution-agent", "version": __version__,
                       "python": platform.python_version(), "platform": sys.platform,
                       "project_commit": commit,
                       "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "request": {"sha256": r.request_sha256, "snapshot": req.model_dump()},
        "base": r.fields["base"],
        "adapter": r.adapter_identity,
        "prompt": r.fields["prompt"],
        "response": r.fields["response"],
        "parsed_response": r.fields["parsed_response"],
        "policy": {"hard_caps": {k: getattr(caps, k) for k in dir(caps) if k.isupper()},
                   "request_limits": req.limits.model_dump(), "decisions": r.decisions},
        "patch": r.fields["patch"],
        "apply": r.fields["apply"],
        "execution": {"boundary_verified": False, "candidate_executed": False,
                      "reason": "Milestone 1 has no verified disposable execution environment",
                      "requested_limits": req.execution.model_dump()},
        "tests": {"identity": {"path": pinned["trusted_test"]["path"], "sha256": pinned["trusted_test"]["sha256"],
                               "test_ids": pinned["trusted_test"]["test_ids"],
                               "expected_base_failures": pinned["trusted_test"]["expected_base_failures"]},
                  "baseline": None, "candidate": None},
        "trace": r.trace,
        "human_review": {"required": True, "pr_creation_authorized": False, "merge_authorized": False,
                         "note": "Draft PR creation requires explicit operator approval; no automatic merge."},
        "limitations": LIMITATIONS_M1,
        "unavailable": unavailable,
    }
