"""Synthetic execution authorization (M2 amendment 1): bind to exact bytes, not to a name or run kind.

Before any `docker create`, the request, the adapter identity, the raw fixture bytes, the validated patch, the
policy-computed new content and the pinned base must all match one entry of the hand-reviewed allowlist, whose own
hash is pinned in demo/pinned.json. Live and replayed responses are refused whatever their bytes.
"""
from __future__ import annotations

import hashlib
import json

from .demo_repo import PROJECT_ROOT

ALLOWLIST_LABEL = "HAND-REVIEWED synthetic fixtures approved for trusted synthetic execution only"
REVIEWED_LABEL = ("OPERATOR-APPROVED recorded model outputs: each entry authorizes replay execution of exactly one "
                  "reviewed proposal, bound by hash")


class NotAuthorized(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def load_allowlist(pinned: dict) -> dict:
    spec = pinned["synthetic_allowlist"]
    raw = (PROJECT_ROOT / spec["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
        raise NotAuthorized("allowlist_hash_mismatch")
    data = json.loads(raw)
    if data.get("label") != ALLOWLIST_LABEL:
        raise NotAuthorized("allowlist_label_invalid")
    return data


def load_reviewed(pinned: dict) -> dict:
    spec = pinned["reviewed_model_outputs"]
    raw = (PROJECT_ROOT / spec["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != spec["sha256"]:
        raise NotAuthorized("reviewed_outputs_hash_mismatch")
    data = json.loads(raw)
    if data.get("label") != REVIEWED_LABEL or not isinstance(data.get("entries"), list):
        raise NotAuthorized("reviewed_outputs_label_invalid")
    return data


def _authorize_replay(*, adapter_identity, response_sha256, patch_sha256, new_content_sha256, base_commit,
                      allowlisted_file, pinned, prescreen_passed) -> dict:
    """A replay executes only if an operator-approved entry matches every hash exactly (Codex M3 amendment 5)."""
    data = load_reviewed(pinned)
    want = {"recording_sha256": adapter_identity.get("recording_sha256"),
            "live_run_id": adapter_identity.get("live_run_id"), "response_sha256": response_sha256,
            "patch_sha256": patch_sha256, "new_content_sha256": new_content_sha256, "base_commit": base_commit,
            "allowlisted_file": allowlisted_file}
    entries = [e for e in data["entries"] if e.get("recording_sha256") == want["recording_sha256"]]
    if not entries:
        raise NotAuthorized("replay_not_approved")
    if len(entries) != 1:
        raise NotAuthorized("replay_approval_ambiguous")
    entry = entries[0]
    for field, actual in want.items():
        if entry.get(field) != actual:
            raise NotAuthorized(f"replay_{field}_mismatch")
    if base_commit != pinned["base_commit"]:
        raise NotAuthorized("base_commit_not_pinned")
    approval = entry.get("operator_approval") or {}
    if approval.get("approved") is not True or not approval.get("approved_by") or not approval.get("approved_at") \
            or approval.get("reviewed_whole_resulting_file") is not True:
        raise NotAuthorized("replay_operator_approval_missing")
    if not prescreen_passed:
        raise NotAuthorized("replay_prescreen_failed")
    return entry


def authorize(*, request_kind: str, adapter_identity: dict, response_sha256: str, patch_sha256: str,
              new_content_sha256: str, base_commit: str, allowlisted_file: str, pinned: dict,
              prescreen_passed: bool = False) -> dict:
    """Return the matching allowlist entry or raise NotAuthorized with a specific detail."""
    kind = adapter_identity.get("kind")
    if "live_model" in (request_kind, kind):
        raise NotAuthorized("untrusted_execution_not_enabled")  # live responses are never executed
    if request_kind != kind:
        raise NotAuthorized("adapter_kind_mismatch")
    if kind == "replay_recording":
        return _authorize_replay(adapter_identity=adapter_identity, response_sha256=response_sha256,
                                 patch_sha256=patch_sha256, new_content_sha256=new_content_sha256,
                                 base_commit=base_commit, allowlisted_file=allowlisted_file, pinned=pinned,
                                 prescreen_passed=prescreen_passed)
    if kind != "synthetic_fixture":
        raise NotAuthorized("untrusted_execution_not_enabled")
    data = load_allowlist(pinned)
    if data.get("base_commit") != base_commit or base_commit != pinned["base_commit"]:
        raise NotAuthorized("base_commit_not_allowlisted")
    if data.get("allowlisted_file") != allowlisted_file:
        raise NotAuthorized("allowlisted_file_mismatch")
    case = adapter_identity.get("fixture_case")
    entries = [e for e in data["entries"] if e["case_id"] == case]
    if len(entries) != 1:
        raise NotAuthorized("case_not_allowlisted")
    entry = entries[0]
    for field, actual in (("fixture_sha256", adapter_identity.get("fixture_sha256")),
                          ("response_sha256", response_sha256), ("patch_sha256", patch_sha256),
                          ("new_content_sha256", new_content_sha256)):
        if entry[field] != actual:
            raise NotAuthorized(f"{field}_mismatch")
    return entry
