"""Recordings of live responses and the replay adapter (Milestone 3).

A recording preserves the exact raw response bytes and their provenance (live run id, request id, models, usage,
prompt and template hashes, base commit). A replay is accepted only if the file hash, the raw-text hash, the live run
id and the prompt hash all match; it is labeled as a replay, never as synthetic, and never executes without a separate
exact-hash operator approval (see authorize.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import caps
from .adapters import AdapterError
from .demo_repo import PROJECT_ROOT
from .models import SchemaError, load_json_strict

SCHEMA = "ira.recording.v1"
RECORDING_LABEL = "RECORDED LIVE MODEL RESPONSE: real model output; not synthetic"
LOCAL_RECORDING_LABEL = "RECORDED LOCAL MODEL RESPONSE: normalized diff; raw model output in provenance"
REPLAY_LABEL = "REPLAY of a recorded model response; see source provenance"
_FIELDS = {"schema_version", "label", "live_run_id", "raw_text", "raw_sha256", "raw_bytes", "prompt_sha256",
           "prompt_template_version", "prompt_template_sha256", "request_sha256", "base_commit", "live",
           "recorded_at"}


def write_recording(directory: Path, *, run_id: str, raw: bytes, live_record: dict, prompt_sha256: str,
                    template_version: str, template_sha256: str, request_sha256: str, base_commit: str) -> tuple:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.recording.json"
    if path.exists():
        raise FileExistsError("recording already exists for this run id")
    label = LOCAL_RECORDING_LABEL if live_record.get("provider") == "local Ollama" else RECORDING_LABEL
    body = {"schema_version": SCHEMA, "label": label, "live_run_id": run_id,
            "raw_text": raw.decode("utf-8"), "raw_sha256": hashlib.sha256(raw).hexdigest(), "raw_bytes": len(raw),
            "prompt_sha256": prompt_sha256, "prompt_template_version": template_version,
            "prompt_template_sha256": template_sha256, "request_sha256": request_sha256, "base_commit": base_commit,
            "live": {k: v for k, v in live_record.items() if k != "pre_call_estimate"},
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    data = (json.dumps(body, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()
    if len(data) > caps.MAX_RECORDING_BYTES:
        raise ValueError("recording exceeds MAX_RECORDING_BYTES")
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(tmp, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return path, hashlib.sha256(data).hexdigest()


class ReplayAdapter:
    def __init__(self, spec, base_dir: Path = PROJECT_ROOT):
        self.spec, self.calls = spec, 0
        path = Path(spec.recording_path)
        path = path if path.is_absolute() else Path(base_dir) / path
        try:
            with open(path, "rb") as handle:
                data = handle.read(caps.MAX_RECORDING_BYTES + 1)
        except OSError:
            raise AdapterError("replay_recording_unreadable", "recording file cannot be read") from None
        if len(data) > caps.MAX_RECORDING_BYTES:
            raise AdapterError("replay_recording_too_large", "recording exceeds MAX_RECORDING_BYTES")
        if hashlib.sha256(data).hexdigest() != spec.recording_sha256:
            raise AdapterError("replay_recording_hash_mismatch", "recording bytes differ from the requested hash")
        try:
            rec = load_json_strict(data, caps.MAX_RECORDING_BYTES)
        except SchemaError:
            raise AdapterError("replay_recording_malformed", "recording is not strict JSON") from None
        if not isinstance(rec, dict) or set(rec) != _FIELDS or rec.get("schema_version") != SCHEMA \
                or rec.get("label") not in (RECORDING_LABEL, LOCAL_RECORDING_LABEL) or not isinstance(rec.get("raw_text"), str):
            raise AdapterError("replay_recording_malformed", "recording fields or label are not as expected")
        live = rec.get("live")
        if not isinstance(live, dict):
            raise AdapterError("replay_recording_malformed", "missing model provenance")
        if live.get("provider") == "local Ollama":
            model_raw = live.get("raw_model_response")
            if not isinstance(model_raw, str) or hashlib.sha256(model_raw.encode("utf-8")).hexdigest() != live.get("raw_model_sha256"):
                raise AdapterError("replay_model_raw_hash_mismatch", "raw model output does not match its hash")
        raw = rec["raw_text"].encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != rec["raw_sha256"] or len(raw) != rec["raw_bytes"]:
            raise AdapterError("replay_raw_hash_mismatch", "raw text does not match its recorded hash")
        if rec["live_run_id"] != spec.live_run_id:
            raise AdapterError("replay_live_run_id_mismatch", "recording belongs to a different live run")
        self.recording, self._raw = rec, raw

    def identity(self) -> dict:
        live = self.recording["live"]
        return {"kind": "replay_recording", "name": "ReplayAdapter", "label": REPLAY_LABEL,
                "model": live.get("requested_model"), "response_model": live.get("response_model"),
                "live_run_id": self.recording["live_run_id"], "recording_sha256": self.spec.recording_sha256,
                "source_provider": live.get("provider"), "raw_model_sha256": live.get("raw_model_sha256"),
                "original_request_id": live.get("request_id"), "original_message_id": live.get("message_id"),
                "fixture_case": None, "fixture_sha256": None}

    def provenance(self) -> dict:
        rec = self.recording
        return {"live_run_id": rec["live_run_id"], "recording_path": self.spec.recording_path,
                "recording_sha256": self.spec.recording_sha256, "raw_sha256": rec["raw_sha256"],
                "prompt_sha256": rec["prompt_sha256"], "request_sha256_of_live_run": rec["request_sha256"],
                "base_commit": rec["base_commit"], "recorded_at": rec["recorded_at"], "live": rec["live"]}

    def propose(self, prompt: str, max_bytes: int) -> bytes:
        self.calls += 1
        if self.calls > 1:
            raise AdapterError("attempt_limit", "only one proposal per run is allowed")
        if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != self.recording["prompt_sha256"]:
            raise AdapterError("replay_prompt_mismatch",
                               "current prompt differs from the recorded one (issue, base, file, limits or template)")
        return self._raw
