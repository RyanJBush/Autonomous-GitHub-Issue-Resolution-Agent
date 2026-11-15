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


def authorize(*, request_kind: str, adapter_identity: dict, response_sha256: str, patch_sha256: str,
              new_content_sha256: str, base_commit: str, allowlisted_file: str, pinned: dict) -> dict:
    """Return the matching allowlist entry or raise NotAuthorized with a specific detail."""
    if request_kind != "synthetic_fixture" or adapter_identity.get("kind") != "synthetic_fixture":
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
