# issue-resolution-agent

A supervised, bounded MVP: one model proposes one patch for one small issue in one Python file of an explicitly
synthetic demonstration repository. Trusted code validates the proposal and writes a review packet for a human.

**Status: Milestone 1 (offline validation core).** What exists and what does not:

| Capability | Milestone 1 |
|---|---|
| Strict request/response schemas, strict JSON | Implemented and tested |
| Narrow single-file patch policy with exact positions and trusted caps | Implemented and tested |
| Reproducible synthetic demo repository and independent base verification | Implemented and tested |
| Scratch apply + confirmation with controlled git configuration | Implemented and tested (not a sandbox) |
| Review packet (JSON + escaped Markdown + manifest) | Implemented and tested |
| Synthetic fixture adapter and 74-case offline evaluation | Implemented; **all fixtures are hand-written, no model is called** |
| Live model adapter | **Not implemented** (Milestone 3, needs operator approval for any paid call) |
| Execution boundary / sandbox | **Not implemented or verified**; every accepted patch stops at `blocked_boundary_unverified` |
| Trusted test execution (baseline and candidate) | **Not run** (Milestone 2) |
| Hosted CI | Workflow present; see the Actions tab for observed runs — a workflow file is not evidence of a pass |
| Draft PR creation | Not implemented; requires explicit operator approval. Never merges. |

The frozen contract is in [CONTRACT.md](CONTRACT.md); caps and grammar are in [docs/POLICY.md](docs/POLICY.md).

## Before you use it

**Supported environment: Linux only.** The verified environment is Linux with Python 3.12 and git at `/usr/bin/git`
or `/bin/git` (locally on Ubuntu 24.04, and on the hosted `ubuntu-24.04` GitHub Actions runner). Native Windows is
**not supported**: `ira/gitcmd.py` looks for git only on the fixed trusted PATH `/usr/bin:/bin`, and an independent
Windows run (Python 3.13) failed with "git not found on the trusted PATH". macOS is untested. Windows support is out
of scope unless separately requested.

**`ira run` exit code 0 means a packet was written, not that the patch passed.** Every terminal status, including
`rejected_schema`, `rejected_policy`, `apply_failed` and `blocked_boundary_unverified`, exits 0 once its packet is on
disk. Callers must read `terminal_status` (printed on stdout as JSON and stored in `packet.json`) and must not treat
exit 0 as success. An invalid operator request (malformed JSON, schema violation, a limit above a hard cap, or a
run id that already has a packet) exits **2**, prints `request rejected: ...` on stderr, and writes **no packet**.

| Command | Exit 0 | Exit 1 | Exit 2 |
|---|---|---|---|
| `ira run` | packet written (any terminal status) | unhandled error, e.g. request file missing (traceback; no packet) | invalid request or usage error; no packet |
| `ira eval` | every fixture case matched | a case mismatched, or unhandled error (e.g. output directory exists) | usage error |
| `ira verify-packet` | manifest and invariants valid | packet invalid | usage error |
| `ira build-demo` | commit equals the pinned commit | commit differs, or unhandled error (e.g. destination exists) | usage error |

Exit 1 is not one error class: read stderr. Exit codes above were observed locally on Linux at the commit that
added this table.

**Do not put secrets in requests or model inputs.** Packets deliberately keep raw material for review: the full
validated request (issue title and body, repository path, limits), the raw model response (up to 32,768 bytes), and
the proposed diff. Nothing is redacted from these. Operators must not place credentials, tokens or other secrets in a
request, in issue text, or in the allowlisted source file. The packet code does not read the process environment, and
a test checks that planted environment variables do not appear in fixture packets; that test is **not** a general
secret-redaction guarantee.

## Pipeline

```
request (operator) -> trusted-test identity -> base verification -> prompt (issue + one file only)
  -> one adapter call -> strict JSON -> base-commit echo -> patch policy -> scratch apply + confirm
  -> execution gate (M1: always refuses) -> packet
```

Terminal statuses are the eleven in contract item 7. Milestone 1 can emit `blocked_boundary_unverified`,
`adapter_error`, `rejected_schema`, `cannot_propose`, `rejected_policy`, `apply_failed` and `execution_error`
(the last only for a failure in trusted code).

## Run it

Linux, Python 3.12+, git at `/usr/bin/git` or `/bin/git`.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

```bash
.venv/bin/python -m pytest -q
```

```bash
.venv/bin/python -m ira eval --out out/eval
```

```bash
.venv/bin/python -m ira build-demo --dest /tmp/ira-demo-repo
```

```bash
.venv/bin/python -m ira run --request demo/request.example.json --out out/runs
```

```bash
.venv/bin/python -m ira verify-packet --dir out/runs/demo-good-001
```

`run` writes `out/runs/<run_id>/{packet.json,review.md,manifest.json}` and refuses to reuse a run id. Check
`terminal_status` in its output; exit 0 alone says nothing about the patch.

## Layout

| Path | Purpose |
|---|---|
| `ira/gitcmd.py` | The only subprocess entry point: trusted git, scrubbed environment, fixed subcommands |
| `ira/base.py` | Independent verification of the demo checkout against `demo/pinned.json` |
| `ira/models.py` | Strict JSON loader, request and response schemas |
| `ira/policy.py` | Diff grammar, exact-position application, caps (pure text processing) |
| `ira/scratch.py` | Scratch `git apply` and confirmation |
| `ira/prompt.py` | Prompt template (`ira.prompt.v1`) and its hash |
| `ira/adapters.py` | Synthetic fixture adapter |
| `ira/packet.py`, `ira/sanitize.py` | Review packet and escaping of untrusted text |
| `ira/runner.py`, `ira/evaluate.py`, `ira/cli.py` | Pipeline, offline evaluation, command line |
| `demo/target/` | Source of the synthetic demo repository (built reproducibly; commit `eb455af5…`) |
| `demo/trusted_tests/` | Trusted issue test, pinned by hash; not executed in Milestone 1 |
| `fixtures/` | Synthetic responses generated by `tools/make_fixtures.py` (drift-checked by the tests) |

## Reuse and attribution

Patterns adapted (not copied wholesale) from the author's earlier repositories: text escaping from
`RyanJBush/model-regression-detection-system` (`mrds/mdsafe.py`, commit `3086d87`), strict JSON loading from the same
repository, single-attempt and terminal-code structure from `RyanJBush/swarmos` (`f956d12`), and injected-issue
evaluation cases from `RyanJBush/prompt-injection-guardrails` (`7dddb8e`).
