"""Offline evaluation over the synthetic fixture cases (no model, no candidate execution)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import caps, demo_repo, runner
from .adapters import SYNTHETIC_LABEL, load_cases
from .demo_repo import PROJECT_ROOT, load_pinned


def load_issue(variant: str) -> tuple[str, str]:
    name = {"clean": "issue.md", "injected": "issue_injected.md"}[variant]
    text = (PROJECT_ROOT / "demo" / name).read_text(encoding="utf-8")
    title, _, body = text.partition("\n")
    return title.strip(), body.strip()


def make_request(case_id: str, repo_path: str, run_id: str, issue_variant: str = "clean",
                 limits: dict | None = None) -> dict:
    pinned = load_pinned()
    title, body = load_issue(issue_variant)
    return {
        "schema_version": "ira.request.v1",
        "run_id": run_id,
        "repository": {"path": str(repo_path), "identity": pinned["identity"], "base_commit": pinned["base_commit"],
                       "allowlisted_file": pinned["allowlisted_file"]},
        "issue": {"title": title, "body": body},
        "limits": limits or {"max_response_bytes": caps.MAX_RESPONSE_BYTES, "max_patch_bytes": caps.MAX_PATCH_BYTES,
                             "max_hunks": caps.MAX_HUNKS, "max_added_lines": caps.MAX_ADDED_LINES,
                             "max_removed_lines": caps.MAX_REMOVED_LINES,
                             "max_changed_lines": caps.MAX_CHANGED_LINES},
        "attempts": 1,
        "trusted_test": {k: pinned["trusted_test"][k] for k in ("path", "sha256", "test_ids")},
        "execution": {"timeout_s": 60, "memory_mb": 256, "pids": 64, "cpus": 1},
        "adapter": {"kind": "synthetic_fixture", "fixture_case": case_id},
    }


def run_eval(out_dir: Path | str) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    (out / "requests").mkdir()
    rows = []
    with tempfile.TemporaryDirectory(prefix="ira-eval-") as work:
        repo = Path(work) / "demo-repo"
        commit = demo_repo.build(repo)
        for case in load_cases():
            req_path = out / "requests" / f"{case['id']}.json"
            req_path.write_text(json.dumps(make_request(case["id"], repo, case["id"],
                                                        case.get("issue_variant", "clean")), indent=2) + "\n")
            pkt, run_dir = runner.execute(req_path, out / "packets", work_root=Path(work) / "scratch")
            match = (pkt["terminal_status"], pkt["status_reason"]) == (case["expected_status"], case["expected_reason"])
            rows.append({"id": case["id"], "category": case["category"], "expected_status": case["expected_status"],
                         "expected_reason": case["expected_reason"], "status": pkt["terminal_status"],
                         "reason": pkt["status_reason"], "match": match,
                         "expected_m2_status": case.get("expected_m2_status"),
                         "packet": str(run_dir.relative_to(out))})
    summary = {"label": SYNTHETIC_LABEL, "demo_base_commit": commit, "total": len(rows),
               "matched": sum(r["match"] for r in rows), "cases": rows,
               "not_verified_by_this_eval": ["live model adapter", "execution boundary / sandbox", "hosted CI",
                                             "trusted test outcomes (not executed in Milestone 1)"]}
    (out / "eval-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [f"# Evaluation summary ({SYNTHETIC_LABEL})", "", f"Demo base commit: `{commit}`",
             f"Matched {summary['matched']} / {summary['total']} cases.", "",
             "| case | category | expected | observed | match |", "|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['category']} | {r['expected_status']} / {r['expected_reason']} | "
              f"{r['status']} / {r['reason']} | {'yes' if r['match'] else '**NO**'} |" for r in rows]
    lines += ["", "Not verified by this evaluation: " + "; ".join(summary["not_verified_by_this_eval"]) + "."]
    (out / "eval-summary.md").write_text("\n".join(lines) + "\n")
    return summary
