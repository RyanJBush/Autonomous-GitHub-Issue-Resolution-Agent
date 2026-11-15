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
from .authorize import NotAuthorized, authorize
from .base import BaseVerificationError, verify
from .demo_repo import PROJECT_ROOT, load_pinned
from .gitcmd import GitError, run_git
from .models import CannotPropose, RequestError, RunRequest, SchemaError, load_request, parse_response
from .sanitize import bounded
from .scratch import ApplyFailed, apply_and_confirm
from .status import Status

RESIDUAL_RISKS_M2 = [
    "Containers share the host kernel; a kernel, runc or dockerd escape reaches the host and its credentials.",
    "The Docker daemon is rootful and user namespaces are not remapped; uid 65534 inside is uid 65534 outside.",
    "Candidate code runs inside the pytest process and can forge exit codes, JUnit XML and output.",
    "Resource limits are observed and tested with synthetic fixtures, not proven complete.",
    "Passing probes describe this run on this host only; they do not verify isolation for model-generated code.",
    "The runner image id and its self-declared inputs label give traceability, not independent proof that the image "
    "was built from the pinned inputs.",
]

LIMITATIONS_M2 = [
    "Milestone 2: only hand-reviewed synthetic fixtures, bound by exact bytes, can reach the executor.",
    "Model responses require explicit review and authorization before replay execution.",
    "Test results are candidate-influenced evidence for human review, not proof of correctness or safety.",
    "Scratch application is a controlled git file operation, not a sandbox.",
    "A passing result authorizes no PR or merge.",
]

LIMITATIONS_M1 = [
    "Milestone 1: no candidate code is executed; trusted tests are not run.",
    "Offline mode does not execute model-generated code; synthetic fixtures do not measure model quality.",
    "Scratch application is a controlled git file operation, not a sandbox.",
    "No execution boundary (sandbox) is verified by Milestone 1.",
    "A future passing test is evidence for human review only; it authorizes no PR or merge.",
]


def default_adapter_factory(spec):
    if spec.kind == "synthetic_fixture":
        return FixtureAdapter(spec.fixture_case)
    if spec.kind == "local_model":
        from .ollama_adapter import OllamaAdapter
        return OllamaAdapter(spec)
    if spec.kind == "live_model":
        from .live_adapter import LiveAdapter
        return LiveAdapter(spec)
    if spec.kind == "replay_recording":
        from .replay import ReplayAdapter
        return ReplayAdapter(spec)
    raise AdapterError("unknown_adapter_kind", "adapter kind not supported")


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
        self.execution = None  # set when an executor is used
        self.tests = {"baseline": None, "candidate": None}
        self.live = None        # live adapter record (requested/returned model, usage, estimates, errors)
        self.replay = None      # replay provenance (live run id, recording hash, original identities)
        self.recording = None   # recording written by a live run
        self.prescreen = None

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
            work_root: Path | str | None = None, pinned: dict | None = None, executor=None,
            live_approval: dict | None = None, recording_dir: Path | str | None = None,
            single_use=None) -> tuple[dict, Path]:
    raw_request = Path(request_path).read_bytes()
    req = load_request(raw_request)  # RequestError propagates: no run, no packet
    run_dir = Path(out_dir) / req.run_id
    if run_dir.exists():
        raise RequestError("run_id already has a packet: one attempt per run id")
    if req.adapter.kind == "live_model":
        # The caller must pass a packet already checked by approval.verify (operator approval + every binding).
        binds = (live_approval or {}).get("binds") or {}
        if not (live_approval or {}).get("operator_approval", {}).get("approved") is True \
                or binds.get("request_sha256") != hashlib.sha256(raw_request).hexdigest():
            raise RequestError("live call refused: no verified operator approval packet for this exact request")
        if single_use is None or not live_approval.get("approval_id"):
            raise RequestError("live call refused: a single-use ledger and an approval id are required")
    pinned = pinned or load_pinned()
    r = _Run(req, raw_request)
    r.recording_dir = Path(recording_dir) if recording_dir else Path(out_dir) / "recordings"
    if req.adapter.kind == "live_model":
        approval_id = live_approval["approval_id"]
        r.claim = lambda: single_use.claim(approval_id, request_sha256=r.request_sha256, run_id=req.run_id)
    else:
        r.claim = None
    status, reason = Status.EXECUTION_ERROR, "internal_error"
    try:
        status, reason = _pipeline(r, pinned, adapter_factory, Path(work_root or tempfile.gettempdir()), executor)
    except Exception as error:  # trusted-code failure; never candidate execution in M1
        r.event(r.stage, "internal_error", type(error).__name__)
        status, reason = Status.EXECUTION_ERROR, f"internal_error:{type(error).__name__}"
    pkt = _assemble(r, pinned, status, reason, executor is not None)
    problems = packet.check_unavailable_invariant(pkt)
    if problems:
        raise RuntimeError("packet invariant violated: " + "; ".join(problems))
    return pkt, packet.write(run_dir, pkt)


def _pipeline(r: _Run, pinned: dict, adapter_factory, work_root: Path, executor=None):
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
    adapter = None
    try:
        adapter = adapter_factory(req.adapter)
        if req.adapter.kind == "live_model":
            adapter.before_send = r.claim  # consumed immediately before the request, whatever happens next
        r.adapter_identity = {**adapter.identity(), "calls": 0}
        if req.adapter.kind == "replay_recording":
            r.replay = adapter.provenance()
        raw = adapter.propose(text, req.limits.max_response_bytes)
        r.adapter_identity["calls"] = adapter.calls
        if type(raw) is not bytes:
            raise AdapterError("adapter_returned_non_bytes", "adapter must return bytes")
    except AdapterError as error:
        r.adapter_identity["calls"] = getattr(adapter, "calls", 0)
        r.live = getattr(adapter, "record", None)
        r.event("adapter", "error", error.code)
        _unavail_from(r, "response", f"adapter error: {error.code}")
        return Status.ADAPTER_ERROR, error.code
    r.live = getattr(adapter, "record", None)
    if req.adapter.kind == "local_model":
        r.fields["prompt"]["role"] = "intermediate template; actual prompt sent is in adapter.live"
    if req.adapter.kind in ("live_model", "local_model"):
        from .replay import write_recording
        try:
            path, digest = write_recording(r.recording_dir, run_id=req.run_id, raw=raw, live_record=r.live or {},
                                           prompt_sha256=r.fields["prompt"]["prompt_sha256"],
                                           template_version=prompt.TEMPLATE_VERSION,
                                           template_sha256=prompt.TEMPLATE_SHA256,
                                           request_sha256=r.request_sha256, base_commit=vb.base_commit)
            r.recording = {"written": True, "path": str(path), "sha256": digest}
            r.event("adapter", "recording_written", digest[:16])
        except (OSError, ValueError, UnicodeDecodeError) as error:
            r.recording = {"written": False, "reason": f"{type(error).__name__}: {str(error)[:120]}"}
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
                         "new_content_sha256": vp.new_content_sha256,
                         # The whole resulting file, so a reviewer reads its full effects, not only the diff.
                         "new_content": vp.new_content.decode("utf-8"), "new_content_bytes": len(vp.new_content)}
    r.event("policy", "accepted")
    from .prescreen import prescreen
    r.prescreen = prescreen(vp.new_content)
    r.decide("prescreen_limited", "pass" if r.prescreen["passed"] else "flagged",
             None if r.prescreen["passed"] else ",".join(r.prescreen["findings"])[:200])

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

    if executor is None:
        gate = boundary.verify_execution_boundary()
        r.decide("execution_boundary", "fail" if not gate.verified else "pass", gate.code)
        r.event("execution", "blocked", gate.code)
        return Status.BLOCKED_BOUNDARY_UNVERIFIED, gate.code

    # Milestone 2: authorization binds to exact bytes before any docker call.
    try:
        entry = authorize(request_kind=req.adapter.kind, adapter_identity=r.adapter_identity,
                          response_sha256=r.fields["response"]["raw_sha256"], patch_sha256=vp.patch_sha256,
                          new_content_sha256=vp.new_content_sha256, base_commit=vb.base_commit,
                          prescreen_passed=bool(r.prescreen and r.prescreen["passed"]),
                          allowlisted_file=vb.allowlisted_file, pinned=pinned)
    except NotAuthorized as error:
        r.decide("synthetic_execution_authorization", "fail", error.detail)
        r.event("execution", "not_authorized", error.detail)
        r.execution = {"authorization": {"authorized": False, "detail": error.detail}, "candidate_executed": False}
        return Status.BLOCKED_BOUNDARY_UNVERIFIED, f"execution_not_authorized:{error.detail}"
    r.decide("synthetic_execution_authorization", "pass")
    entry_id = entry.get("case_id") or f"reviewed_recording:{entry.get('recording_sha256', '')[:16]}"
    r.event("execution", "authorized", entry_id)
    from .execution import Limits
    rep = executor.run(run_id=req.run_id, repo_path=vb.path, base_commit=vb.base_commit,
                       allowlisted_file=vb.allowlisted_file, new_content=vp.new_content,
                       limits=Limits(**req.execution.model_dump()))
    r.execution = {"authorization": {"authorized": True, "case_id": entry_id,
                                     "reviewed_effect": entry.get("reviewed_effect"),
                                     "operator_approval": entry.get("operator_approval")},
                   "candidate_executed": rep.candidate_executed, "host": rep.host, "image": rep.image,
                   "probe": rep.probe, "containers": rep.containers}
    r.tests = {"baseline": rep.baseline, "candidate": rep.candidate}
    for rec in rep.containers:  # every created container: configuration result, then cleanup result
        if rec["config_failures"]:
            r.event(rec["role"], "config_failed", ",".join(rec["config_failures"]))
        r.event(rec["role"], "cleanup_ok" if rec["cleanup_ok"] else "cleanup_failed", rec["cleanup_detail"][:120])
    r.decide("synthetic_execution", "pass" if rep.status is Status.TESTS_PASSED_AWAITING_HUMAN_REVIEW else "fail",
             rep.reason)
    r.event("execution", "verdict", rep.reason)
    return rep.status, rep.reason


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


def _assemble(r: _Run, pinned: dict, status: Status, reason: str, executed_mode: bool = False) -> dict:
    req = r.req
    for name in _ORDER:
        if r.fields[name] is None and name not in r.unavailable:
            r.unavailable[name] = f"not reached (terminal status {status.value})"
    commit, commit_reason = _project_commit()
    unavailable = dict(r.unavailable)
    if commit is None:
        unavailable["provenance.project_commit"] = commit_reason
    if not executed_mode:
        not_run = ("offline mode: trusted tests are not executed (no execution requested)"
                   if status is Status.BLOCKED_BOUNDARY_UNVERIFIED else f"not run: terminal status {status.value}")
    else:
        not_run = f"not run: terminal status {status.value} ({reason})"
    for stage in ("baseline", "candidate"):
        if r.tests[stage] is None:
            unavailable[f"tests.{stage}"] = not_run
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
        "adapter": {**r.adapter_identity, "live": r.live, "recording": r.recording, "replay_provenance": r.replay},
        "prompt": r.fields["prompt"],
        "response": r.fields["response"],
        "parsed_response": r.fields["parsed_response"],
        "policy": {"hard_caps": {k: getattr(caps, k) for k in dir(caps) if k.isupper()},
                   "request_limits": req.limits.model_dump(), "decisions": r.decisions,
                   "prescreen": r.prescreen},
        "patch": r.fields["patch"],
        "apply": r.fields["apply"],
        "execution": _execution_section(r, req, executed_mode),
        "tests": {"identity": {"path": pinned["trusted_test"]["path"], "sha256": pinned["trusted_test"]["sha256"],
                               "test_ids": pinned["trusted_test"]["test_ids"],
                               "expected_base_failures": pinned["trusted_test"]["expected_base_failures"]},
                  "baseline": r.tests["baseline"], "candidate": r.tests["candidate"]},
        "trace": r.trace,
        "human_review": {"required": True, "pr_creation_authorized": False, "merge_authorized": False,
                         "note": "Draft PR creation requires explicit operator approval; no automatic merge."},
        "limitations": LIMITATIONS_M2 if executed_mode else LIMITATIONS_M1,
        "end_to_end_objective": _objective(kind, status, reason),
        "unavailable": unavailable,
    }


def _execution_section(r: _Run, req, executed_mode: bool) -> dict:
    base = {"mode": "trusted_synthetic_execution" if executed_mode else "offline",
            "verified_for_model_generated_code": False, "candidate_executed": False,
            "requested_limits": req.execution.model_dump()}
    if not executed_mode:
        base["reason"] = "offline mode: no container is started"
        return base
    base.update(r.execution or {"authorization": None, "reason": "execution stage not reached"})
    base["residual_risks"] = RESIDUAL_RISKS_M2
    return base


def _objective(kind: str, status: Status, reason: str) -> dict:
    """Honest end-to-end status (Codex M3 amendment 6): only an evaluated real proposal can meet the objective."""
    if kind == "synthetic_fixture":
        return {"status": "not_applicable_synthetic_run",
                "reason": "hand-written fixture; says nothing about a real model proposal"}
    if kind == "replay_recording" and status is Status.TESTS_PASSED_AWAITING_HUMAN_REVIEW:
        return {"status": "evaluated_awaiting_human_review",
                "reason": "a real recorded proposal passed the trusted tests; human review is still required"}
    what = "model responses are never executed without review" if kind in ("live_model", "local_model") else "the replay was not evaluated to a pass"
    return {"status": "not_met", "reason": f"{what}; observed result: {status.value} ({reason})"}
