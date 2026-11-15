"""Review packet (contract item 6): packet.json, review.md and manifest.json (written last).

Unavailable fields are null and listed in `unavailable` with an explicit reason. Model-derived text is bounded in
packet.json and additionally escaped in review.md. Nothing here reads the process environment.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from . import caps
from .sanitize import code_span, fenced, visible, visible_block

SCHEMA_VERSION = "ira.packet.v1"

REQUIRED_FIELDS = (
    "schema_version", "label", "run_kind", "run_id", "terminal_status", "status_reason", "stage_reached",
    "provenance", "request", "base", "adapter", "prompt", "response", "parsed_response", "policy", "patch",
    "apply", "execution", "tests", "trace", "human_review", "limitations", "unavailable", "end_to_end_objective",
)
# Fields that may be null; each null one must appear in `unavailable` with a reason (and only then).
NULLABLE_FIELDS = ("base", "prompt", "response", "parsed_response", "patch", "apply",
                   "tests.baseline", "tests.candidate", "provenance.project_commit")

RUN_KIND_LABELS = {
    "synthetic_fixture": "SYNTHETIC: hand-written fixture output; no model was called",
    "live_model": "LIVE MODEL RUN",
    "replay_recording": "REPLAY of a recorded live model response",
}


def get_path(packet: dict, dotted: str):
    node = packet
    for part in dotted.split("."):
        node = node[part]
    return node


def check_unavailable_invariant(packet: dict) -> list[str]:
    problems = []
    for name in NULLABLE_FIELDS:
        value = get_path(packet, name)
        reason = packet["unavailable"].get(name)
        if value is None and not (isinstance(reason, str) and reason.strip()):
            problems.append(f"{name} is null without a reason")
        if value is not None and name in packet["unavailable"]:
            problems.append(f"{name} has a value but is listed as unavailable")
    for name in packet["unavailable"]:
        if name not in NULLABLE_FIELDS:
            problems.append(f"unexpected unavailable key {name}")
    missing = [f for f in REQUIRED_FIELDS if f not in packet]
    problems += [f"missing field {f}" for f in missing]
    return problems


def render_markdown(p: dict) -> str:
    L = caps.MAX_REVIEW_FIELD_CHARS
    out = [f"# Review packet: {code_span(p['run_id'], 80)}", "",
           f"> {p['label']}", "",
           f"- Terminal status: **{p['terminal_status']}**",
           f"- Reason: {code_span(p['status_reason'], 120)}",
           f"- Stage reached: {code_span(p['stage_reached'], 60)}",
           f"- Run kind: {code_span(p['run_kind'], 40)}",
           f"- Human review required: yes. PR creation authorized: no. Merge authorized: no.", ""]
    base = p["base"]
    out += ["## Base", ""]
    if base:
        out += [f"- Identity: {code_span(base['identity'], 80)}", f"- Commit: {code_span(base['base_commit'], 40)}",
                f"- Allowlisted file: {code_span(base['allowlisted_file'], 200)}"]
    else:
        out.append(f"- Unavailable: {visible(p['unavailable'].get('base'), L)}")
    obj = p["end_to_end_objective"]
    out += ["## End-to-end objective", "", f"- Status: **{obj['status']}**", f"- {visible(obj['reason'], L)}", ""]
    ad = p["adapter"]
    out += ["## Adapter", "", f"- Kind: {code_span(ad.get('kind'), 40)}; name: {code_span(ad.get('name'), 80)};"
            f" model: {code_span(ad.get('model'), 80)}; calls: {ad.get('calls')}"]
    live = ad.get("live")
    if live:
        out += [f"- Requested model: {code_span(live.get('requested_model'), 64)}; returned model: "
                f"{code_span(live.get('response_model'), 64)}; request id: {code_span(live.get('request_id'), 80)}",
                f"- Request sent: {live.get('request_sent')}; outcome: {code_span(live.get('adapter_outcome'), 80)}; "
                f"stop reason: {code_span(live.get('stop_reason'), 40)}",
                f"- max_tokens {live.get('max_tokens')}, effort {code_span(live.get('effort'), 10)}, retries 0, "
                f"tools none, fallbacks none; cost cap ${live.get('cost_cap_usd')}",
                f"- Usage: {code_span(json.dumps(live.get('usage'), sort_keys=True), 300)}"]
        est = live.get("pre_call_estimate")
        if est:
            out.append(f"- Pre-call cost ESTIMATE: ${est['total_usd_estimate']} (not a bound; "
                       f"prices from {est['price_source']} retrieved {est['price_retrieved']})")
        if live.get("cost_from_usage"):
            out.append(f"- Cost ESTIMATE from reported usage: ${live['cost_from_usage']['total_usd_estimate']} "
                       "(the provider invoice is authoritative)")
    rec = ad.get("recording")
    if rec:
        out.append(f"- Recording: {code_span(rec.get('path') or rec.get('reason'), 300)} "
                   f"sha256 {code_span(rec.get('sha256'), 64)}")
    prov = ad.get("replay_provenance")
    if prov:
        out += [f"- Replay of live run {code_span(prov['live_run_id'], 64)}; recording sha256 "
                f"{code_span(prov['recording_sha256'], 64)}; original request id "
                f"{code_span((prov.get('live') or {}).get('request_id'), 80)}"]
    pr = p["parsed_response"]
    out += ["", "## Model response (untrusted)", ""]
    if pr:
        out.append(f"- Outcome: {code_span(pr['outcome'], 40)}")
        if pr["outcome"] == "propose":
            out.append(f"- Summary: {visible(pr['summary'], L)}")
            out += [f"- Assumption: {visible(a, L)}" for a in pr["assumptions"]]
        else:
            out.append(f"- Reason: {visible(pr['reason'], L)}")
    else:
        out.append(f"- Parsed response unavailable: {visible(p['unavailable'].get('parsed_response'), L)}")
    if p["response"]:
        text, cut = visible_block(p["response"]["stored_text"], 4000)
        out += ["", f"Raw response (bounded, escaped{', truncated' if cut else ''}; sha256 "
                    f"{p['response']['raw_sha256']}):", "", fenced(text, "text")]
    out += ["", "## Policy decisions", ""]
    out += [f"- {code_span(d['check'], 60)}: {d['result']}" + (f" ({code_span(d.get('code'), 80)})" if d.get('code') else "")
            for d in p["policy"]["decisions"]]
    out += ["", "## Patch", ""]
    if p["patch"]:
        pt = p["patch"]
        text, _ = visible_block(pt["diff"], caps.MAX_PATCH_BYTES)
        out += [f"- sha256 {pt['sha256']}; {pt['bytes']} bytes; {pt['hunks']} hunk(s); +{pt['added']} -{pt['removed']}",
                "", fenced(text, "diff")]
        if pt.get("new_content") is not None:
            whole, _ = visible_block(pt["new_content"], caps.MAX_SOURCE_FILE_BYTES)
            out += ["", f"Whole resulting file {code_span(pt['path'], 200)} (sha256 {pt['new_content_sha256']}; "
                        "review all of it, not only the diff):", "", fenced(whole, "python")]
        ps = p["policy"].get("prescreen")
        if ps:
            out.append(f"- {ps['label']}: passed {ps['passed']}; findings "
                       f"{code_span(','.join(ps['findings']) or 'none', 300)}")
    else:
        out.append(f"- Unavailable: {visible(p['unavailable'].get('patch'), L)}")
    ex = p["execution"]
    out += ["", "## Execution and tests", "",
            f"- Mode: {code_span(ex['mode'], 60)}; candidate executed: {ex['candidate_executed']}; "
            f"verified for model-generated code: **{ex['verified_for_model_generated_code']}**",
            f"- Trusted test: {code_span(p['tests']['identity']['path'], 200)} sha256 {p['tests']['identity']['sha256']}"]
    auth = ex.get("authorization")
    if auth:
        out.append(f"- Synthetic execution authorization: {auth.get('authorized')} "
                   f"({code_span(auth.get('case_id') or auth.get('detail'), 120)})")
    if ex.get("probe"):
        out.append(f"- Boundary probe passed: {ex['probe']['passed']}; failed checks: "
                   f"{code_span(','.join(ex['probe']['failed_checks']) or 'none', 300)}")
    if ex.get("host"):
        h = ex["host"]
        out.append(f"- Host: docker {code_span(h.get('docker_server_version'), 40)}, cgroup v{h.get('cgroup_version')}, "
                   f"kernel {code_span(h.get('kernel'), 60)}")
    for stage in ("baseline", "candidate"):
        t = p["tests"][stage]
        if t is None:
            out.append(f"- {stage.title()}: {visible(p['unavailable'].get('tests.' + stage), L)}")
            continue
        out.append(f"- {stage.title()}: verdict {code_span(t['verdict'], 60)} ({code_span(t['reason'], 120)}); "
                   f"pytest exit {t['pytest_exit']}; container exit {t['exit_code']}; timed out {t['timed_out']}; "
                   f"cleanup ok {t['cleanup_ok']}; {t['duration_s']} s")
        for tid, outcome in sorted((t.get("tests") or {}).items()):
            out.append(f"  - {code_span(tid, 120)}: {outcome}")
    for c in ex.get("containers") or []:
        text, cut = visible_block(c["stderr"]["text"], 2000)
        out += ["", f"Container {code_span(c['role'], 20)} stderr (candidate-influenced, bounded, escaped"
                    f"{', truncated' if cut or c['stderr']['truncated'] else ''}; {c['stderr']['bytes']} bytes):",
                "", fenced(text, "text")]
    for risk in ex.get("residual_risks") or []:
        out.append(f"- Residual risk: {risk}")
    out += ["", "## Trace", ""]
    out += [f"{t['seq']}. {code_span(t['stage'], 40)} {code_span(t['event'], 80)}" for t in p["trace"]]
    out += ["", "## Limitations", ""] + [f"- {x}" for x in p["limitations"]]
    return "\n".join(out) + "\n"


def _atomic_write(path: Path, content: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def serialize(packet: dict) -> dict[str, bytes]:
    return {"packet.json": (json.dumps(packet, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode(),
            "review.md": render_markdown(packet).encode("utf-8")}


def write(run_dir: Path, packet: dict) -> Path:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)  # one attempt per run id: never overwrite
    files = serialize(packet)
    for name, content in files.items():
        _atomic_write(run_dir / name, content)
    manifest = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    _atomic_write(run_dir / "manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return run_dir


def verify(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    if set(manifest) != {"packet.json", "review.md"}:
        raise ValueError("manifest lists unexpected files")
    for name, digest in manifest.items():
        if hashlib.sha256((run_dir / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"manifest mismatch: {name}")
    packet = json.loads((run_dir / "packet.json").read_text())
    problems = check_unavailable_invariant(packet)
    if problems:
        raise ValueError("; ".join(problems))
    return packet
