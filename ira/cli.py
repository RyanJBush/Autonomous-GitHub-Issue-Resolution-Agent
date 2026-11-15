"""Command line: python -m ira {build-demo,run,eval,verify-packet}.

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
    e = sub.add_parser("eval", help="run every synthetic fixture case")
    e.add_argument("--out", required=True)
    v = sub.add_parser("verify-packet", help="check a packet directory's manifest and invariants")
    v.add_argument("--dir", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "build-demo":
        commit = demo_repo.build(args.dest)
        pinned = demo_repo.load_pinned()["base_commit"]
        print(json.dumps({"commit": commit, "matches_pinned": commit == pinned}))
        return 0 if commit == pinned else 1
    if args.cmd == "run":
        try:
            pkt, run_dir = runner.execute(args.request, args.out)
        except RequestError as error:
            print(f"request rejected: {error}", file=sys.stderr)
            return 2
        print(json.dumps({"run_id": pkt["run_id"], "run_kind": pkt["run_kind"],
                          "terminal_status": pkt["terminal_status"], "status_reason": pkt["status_reason"],
                          "packet": str(run_dir)}))
        return 0
    if args.cmd == "eval":
        summary = evaluate.run_eval(args.out)
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
