"""Strict schemas: the operator request (trusted, still validated) and the model response (untrusted)."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

from . import caps

HEX40 = r"^[0-9a-f]{40}$"
HEX64 = r"^[0-9a-f]{64}$"
_SAFE_REL_PY = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]*/)*[A-Za-z0-9_][A-Za-z0-9_.-]*\.py$")
_UNSAFE_TEXT_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


class SchemaError(Exception):
    """Untrusted response failed strict parsing. `errors` never contains input values."""

    def __init__(self, code: str, detail: str, errors: list | None = None):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.errors = code, detail, errors or []


class RequestError(Exception):
    """The operator request is invalid. No run is started and no packet is written."""


# --------------------------------------------------------------------------- strict JSON
def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise SchemaError("duplicate_key", "duplicate JSON object key")
        out[key] = value
    return out


def _no_constants(name):
    raise SchemaError("non_finite_number", f"non-standard JSON constant {name}")


def load_json_strict(raw: bytes, max_bytes: int):
    if type(raw) is not bytes:
        raise SchemaError("not_bytes", "response must be bytes")
    if len(raw) > max_bytes:
        raise SchemaError("response_too_large", f"response exceeds {max_bytes} bytes")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise SchemaError("bom_not_allowed", "UTF-8 byte order mark is not allowed")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SchemaError("not_utf8", "response is not valid UTF-8") from None
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except SchemaError:
        raise
    except (ValueError, RecursionError) as error:
        raise SchemaError("invalid_json", f"not a single JSON document ({type(error).__name__})") from None


def _safe_errors(error: ValidationError) -> list:
    return [{"loc": [str(p) for p in e.get("loc", ())][:6], "type": str(e.get("type", ""))[:60]}
            for e in error.errors(include_input=False, include_url=False, include_context=False)][:20]


def _plain_text(value: str) -> str:
    for ch in value:
        if unicodedata.category(ch) in _UNSAFE_TEXT_CATEGORIES:
            raise ValueError("control, format or unassigned characters are not allowed")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --------------------------------------------------------------------------- model response (untrusted)
Text500 = Annotated[str, Field(min_length=1, max_length=caps.MAX_SUMMARY_CHARS)]


class Proposal(_Strict):
    schema_version: Literal["ira.response.v1"]
    outcome: Literal["propose"]
    base_commit: Annotated[str, Field(pattern=HEX40)]
    diff: Annotated[str, Field(min_length=1)]  # size, grammar and characters are enforced by policy.py
    summary: Text500
    assumptions: Annotated[list[Annotated[str, Field(min_length=1, max_length=caps.MAX_ASSUMPTION_CHARS)]],
                           Field(max_length=caps.MAX_ASSUMPTIONS)]

    @field_validator("summary")
    @classmethod
    def _summary(cls, v):
        return _plain_text(v)

    @field_validator("assumptions")
    @classmethod
    def _assumptions(cls, v):
        return [_plain_text(a) for a in v]


class CannotPropose(_Strict):
    schema_version: Literal["ira.response.v1"]
    outcome: Literal["cannot_propose"]
    reason: Annotated[str, Field(min_length=1, max_length=caps.MAX_REASON_CHARS)]

    @field_validator("reason")
    @classmethod
    def _reason(cls, v):
        return _plain_text(v)


_RESPONSE = TypeAdapter(Annotated[Union[Proposal, CannotPropose], Field(discriminator="outcome")])


def parse_response(raw: bytes, max_bytes: int) -> Proposal | CannotPropose:
    data = load_json_strict(raw, max_bytes)
    if not isinstance(data, dict):
        raise SchemaError("not_object", "response must be a JSON object")
    try:
        return _RESPONSE.validate_python(data, strict=True)
    except ValidationError as error:
        raise SchemaError("schema_violation", "response does not match ira.response.v1", _safe_errors(error)) from None


# --------------------------------------------------------------------------- operator request (trusted input)
def _cap(name, cap):
    return Annotated[int, Field(ge=1, le=cap, description=f"{name} (hard cap {cap})")]


class Limits(_Strict):
    max_response_bytes: _cap("response bytes", caps.MAX_RESPONSE_BYTES)
    max_patch_bytes: _cap("patch bytes", caps.MAX_PATCH_BYTES)
    max_hunks: _cap("hunks", caps.MAX_HUNKS)
    max_added_lines: _cap("added lines", caps.MAX_ADDED_LINES)
    max_removed_lines: _cap("removed lines", caps.MAX_REMOVED_LINES)
    max_changed_lines: _cap("changed lines", caps.MAX_CHANGED_LINES)


class Issue(_Strict):
    title: Annotated[str, Field(min_length=1, max_length=caps.MAX_ISSUE_TITLE_CHARS)]
    body: Annotated[str, Field(min_length=1, max_length=caps.MAX_ISSUE_BODY_CHARS)]


class RepositorySpec(_Strict):
    path: Annotated[str, Field(min_length=1, max_length=4096)]
    identity: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$")]
    base_commit: Annotated[str, Field(pattern=HEX40)]
    allowlisted_file: Annotated[str, Field(max_length=200)]

    @field_validator("allowlisted_file")
    @classmethod
    def _path(cls, v):
        parts = v.split("/")
        if not _SAFE_REL_PY.match(v) or any(p in ("", ".", "..") or p.startswith(".") for p in parts):
            raise ValueError("allowlisted_file must be a safe relative POSIX path to a .py file")
        if parts[0] in ("tests", "test", ".github") or parts[-1].startswith("test_") or parts[-1] == "conftest.py":
            raise ValueError("allowlisted_file must be a source file, not a test or workflow file")
        return v


class TrustedTest(_Strict):
    path: Annotated[str, Field(min_length=1, max_length=200)]
    sha256: Annotated[str, Field(pattern=HEX64)]
    test_ids: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], Field(min_length=1, max_length=20)]


class ExecutionLimits(_Strict):
    """Recorded in M1; enforced by the execution stage in M2."""
    timeout_s: Annotated[int, Field(ge=1, le=600)]
    memory_mb: Annotated[int, Field(ge=64, le=4096)]
    pids: Annotated[int, Field(ge=16, le=1024)]
    cpus: Annotated[int, Field(ge=1, le=4)]


class SyntheticAdapterSpec(_Strict):
    kind: Literal["synthetic_fixture"]
    fixture_case: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")]


class LiveAdapterSpec(_Strict):
    """One Messages API request: no tools, no retries, no fallback. Executed responses: never."""
    kind: Literal["live_model"]
    model: Annotated[str, Field(min_length=1, max_length=64)]
    max_tokens: Annotated[int, Field(ge=256, le=caps.LIVE_MAX_TOKENS)]
    effort: Literal["low", "medium", "high"]
    timeout_s: Annotated[int, Field(ge=10, le=caps.LIVE_TIMEOUT_S)]
    estimate_threshold_cents: Annotated[int, Field(ge=1, le=caps.LIVE_ESTIMATE_THRESHOLD_CENTS)]
    authorized_response_models: Annotated[list[Annotated[str, Field(min_length=1, max_length=64)]],
                                          Field(min_length=1, max_length=3)]

    @model_validator(mode="after")
    def _models(self):
        if self.model not in caps.LIVE_MODELS:
            raise ValueError("model is not in the trusted live-model allowlist")
        if self.model not in self.authorized_response_models:
            raise ValueError("authorized_response_models must include the requested model")
        return self


class LocalAdapterSpec(_Strict):
    kind: Literal["local_model"]
    model: Literal["qwen3:4b", "qwen3-coder:latest"]
    model_digest: Annotated[str, Field(pattern=HEX64)]
    timeout_s: Annotated[int, Field(ge=10, le=600)]


class ReplayAdapterSpec(_Strict):
    kind: Literal["replay_recording"]
    recording_path: Annotated[str, Field(min_length=1, max_length=4096)]
    recording_sha256: Annotated[str, Field(pattern=HEX64)]
    live_run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]


AdapterSpec = Annotated[Union[SyntheticAdapterSpec, LiveAdapterSpec, LocalAdapterSpec, ReplayAdapterSpec], Field(discriminator="kind")]


class RunRequest(_Strict):
    schema_version: Literal["ira.request.v1"]
    run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
    repository: RepositorySpec
    issue: Issue
    limits: Limits
    attempts: Literal[1]
    trusted_test: TrustedTest
    execution: ExecutionLimits
    adapter: AdapterSpec

    @model_validator(mode="after")
    def _changed(self):
        if self.limits.max_changed_lines > self.limits.max_added_lines + self.limits.max_removed_lines:
            raise ValueError("max_changed_lines cannot exceed max_added_lines + max_removed_lines")
        return self


def load_request(raw: bytes) -> RunRequest:
    """Operator request: same strict JSON rules; failures raise RequestError (no packet)."""
    try:
        data = load_json_strict(raw, 65_536)
    except SchemaError as error:
        raise RequestError(f"request JSON invalid: {error.code}") from None
    try:
        return RunRequest.model_validate(data, strict=True)
    except ValidationError as error:
        raise RequestError(f"request invalid: {_safe_errors(error)}") from None
