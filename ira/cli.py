"""Command line: python -m ira {build-demo,build-runner-image,run,eval,verify-packet,prepare-live-approval,
check-operator-env}.

Exit codes: 0 success (run: a packet was written, whatever its status; eval: every case matched),
1 eval mismatch or packet verification failure, 2 invalid request or usage.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import demo_repo, evaluate, packet, runner
from .models import RequestError


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ira")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-demo", help="build the synthetic demonstration repository")
    b.add_argument("--dest", required=True)
    r = sub.add_parser("run", help="one bounded run from a request file")
    r.add_argument("--request", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--execute", action="store_true",
                   help="trusted synthetic execution in Docker (allowlisted fixtures only)")
    r.add_argument("--live", action="store_true", help="one paid model request; requires --approval-packet")
    r.add_argument("--approval-packet", help="operator-approved packet from prepare-live-approval")
    r.add_argument("--spent-ledger", help="single-use ledger directory outside the checkout "
                                          "(default: $XDG_STATE_HOME or ~/.local/state, then ira/live-approvals)")
    r.add_argument("--api-key-prompt", action="store_true",
                   help="live only, required: read the API key with a hidden prompt (never from args or env)")
    c = sub.add_parser("check-operator-env", help="verify the operator machine for the one live call (no key, no network)")
    c.add_argument("--request", default="demo/live/request.json")
    c.add_argument("--spent-ledger")
    a = sub.add_parser("prepare-live-approval", help="write the approval packet for one live request (no network)")
    a.add_argument("--request", required=True)
    a.add_argument("--out", required=True)
    e = sub.add_parser("eval", help="run every synthetic fixture case")
    e.add_argument("--out", required=True)
    e.add_argument("--execute", action="store_true", help="compare against execute-mode expectations")
    sub.add_parser("build-runner-image", help="build the pinned runner image (docker build --network none)")
    v = sub.add_parser("verify-packet", help="check a packet directory's manifest and invariants")
    v.add_argument("--dir", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "build-demo":
        commit = demo_repo.build(args.dest)
        pinned = demo_repo.load_pinned()["base_commit"]
        print(json.dumps({"commit": commit, "matches_pinned": commit == pinned}))
        return 0 if commit == pinned else 1
    if args.cmd == "build-runner-image":
        from . import imagebuild
        print(json.dumps(imagebuild.build()))
        return 0
    if args.cmd == "check-operator-env":
        from .operator_check import run_checks
        rows = run_checks(args.request, args.spent_ledger)
        for row in rows:
            print(f"{'PASS' if row['ok'] else 'FAIL'}  {row['check']}: {row['detail']}")
        failed = [r["check"] for r in rows if not r["ok"]]
        print("operator environment: " + ("READY (no key was read and no request was made)" if not failed
                                          else f"NOT READY ({len(failed)} failed)"))
        return 0 if not failed else 1
    if args.cmd == "prepare-live-approval":
        from . import approval
        try:
            pkt = approval.prepare(args.request, args.out)
        except (approval.ApprovalError, RequestError) as error:
            print(f"approval packet not written: {error}", file=sys.stderr)
            return 2
        print(json.dumps({"status": pkt["status"], "request_sha256": pkt["binds"]["request_sha256"],
                          "estimate_usd": pkt["binds"]["estimate"]["total_usd_estimate"], "out": args.out}))
        return 0
    if args.cmd == "run":
        executor, live_approval = None, None
        from .models import load_request as _load
        try:
            kind = _load(open(args.request, "rb").read()).adapter.kind
        except RequestError as error:
            print(f"request rejected: {error}", file=sys.stderr)
            return 2
        if (kind == "live_model") != bool(args.live):
            print("request rejected: --live is required for, and only for, a live_model request", file=sys.stderr)
            return 2
        if args.live:
            from . import approval
            if not args.approval_packet:
                print("request rejected: --live requires --approval-packet", file=sys.stderr)
                return 2
            from pathlib import Path as _P
            from .demo_repo import PROJECT_ROOT
            from .single_use import LedgerError, SingleUseLedger, default_ledger_dir
            if _P(args.out).resolve().is_relative_to(PROJECT_ROOT):
                print("live call refused before any request: --out must be outside the checkout", file=sys.stderr)
                return 2
            import os as _os
            present = [v for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if _os.environ.get(v)]
            if not args.api_key_prompt or present:
                print("live call refused before any request: use --api-key-prompt and unset "
                      + (", ".join(present) if present else "any ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN"),
                      file=sys.stderr)
                return 2
            if not sys.stdin.isatty():
                print("live call refused before any request: an interactive terminal is required", file=sys.stderr)
                return 2
            try:
                live_approval = approval.verify(args.approval_packet, args.request)
                ledger = SingleUseLedger(args.spent_ledger or default_ledger_dir())
                if ledger.is_used(live_approval["approval_id"]):
                    raise approval.ApprovalError("approval_already_used")
            except (approval.ApprovalError, RequestError, LedgerError) as error:
                print(f"live call refused before any request: {error}", file=sys.stderr)
                return 2
            import getpass
            from .live_adapter import LiveAdapter, prompted_client_factory
            key = getpass.getpass("Anthropic API key (input hidden; it is not stored): ").strip()
            if not key.startswith("sk-ant-"):
                print("live call refused before any request: input does not look like an Anthropic API key "
                      "(it was not echoed or stored)", file=sys.stderr)
                return 2
            live_factory = prompted_client_factory(key)
            del key  # the factory closure keeps the only reference, in this process's memory
        if args.execute:
            from .synthetic_exec import DockerExecutor
            executor = DockerExecutor(demo_repo.load_pinned())
        try:
            kwargs = {}
            if args.live:
                kwargs = {"adapter_factory": lambda spec: LiveAdapter(spec, client_factory=live_factory),
                          "single_use": ledger}
            pkt, run_dir = runner.execute(args.request, args.out, executor=executor, live_approval=live_approval,
                                          **kwargs)
        except RequestError as error:
            print(f"request rejected: {error}", file=sys.stderr)
            return 2
        print(json.dumps({"run_id": pkt["run_id"], "run_kind": pkt["run_kind"],
                          "terminal_status": pkt["terminal_status"], "status_reason": pkt["status_reason"],
                          "end_to_end_objective": pkt["end_to_end_objective"]["status"], "packet": str(run_dir)}))
        return 0
    if args.cmd == "eval":
        summary = evaluate.run_eval(args.out, execute=args.execute)
        print(f"{summary['label']}: matched {summary['matched']}/{summary['total']}")
        for row in summary["cases"]:
            if not row["match"]:
                print(f"MISMATCH {row['id']}: expected {row['expected_status']}/{row['expected_reason']} "
                      f"got {row['status']}/{row['reason']}")
        return 0 if summary["matched"] == summary["total"] else 1
    if args.cmd == "verify-packet":
        try:
            pkt = packet.verify(args.dir)
        except (ValueError, OSError, KeyError) as error:
            print(f"packet invalid: {error}", file=sys.stderr)
            return 1
        print(json.dumps({"run_id": pkt["run_id"], "terminal_status": pkt["terminal_status"], "valid": True}))
        return 0
    return 2
