# Autonomous GitHub Issue Resolution Agent

**Supervised AI Code Repair with Bounded Patches and Human Review**

A supervised coding-agent prototype that generates bounded patch proposals for a synthetic GitHub-style issue,
validates them against strict policies, and produces verifiable evidence for human review. "Autonomous" is the
project's established name, not a description of the workflow: model output is never executed without an
exact-hash operator approval, and the tool never opens or merges pull requests.

In the demonstration, a local model proposes a bounded change to one
Python function, policy checks produce a diff, and trusted tests evaluate a separately reviewed
candidate. This MVP uses a **synthetic issue and repository**; it does not fetch GitHub issues or
autonomously open pull requests.

In the recorded run, local `qwen3:4b` proposed a return statement for whitespace normalization.
The adapter derived the diff, and a separate supervised Docker evaluation observed two expected
baseline failures followed by three candidate passes. The prompt contained hints informed by the
trusted tests, so this is a **guided demonstration**, not a blind benchmark. The original agent run
stopped before execution; the retained test output is candidate-influenced, and full container
inspection logs for the separate evaluation were not retained.

[Review the result](docs/evidence/local-qwen3-011/review-packet.md) ·
[See the original proposal](docs/evidence/local-qwen3-011/review.md) ·
[Run the demo](docs/DEMO.md) · [Read the limitations](docs/LOCAL_RUN.md) ·
[View hosted CI](https://github.com/RyanJBush/Autonomous-GitHub-Issue-Resolution-Agent/actions/workflows/ci.yml)

**Status: one real local-model proposal was separately evaluated on the synthetic demonstration issue.** The Anthropic adapter remains offline-only and has not made a real request. The local model run and evaluation have the limitations below:

| Capability | Current state |
|---|---|
| Strict request/response schemas, strict JSON | Implemented and tested |
| Narrow single-file patch policy with exact positions and trusted caps | Implemented and tested |
| Reproducible synthetic demo repository and independent base verification | Implemented and tested |
| Scratch apply + confirmation with controlled git configuration | Implemented and tested (not a sandbox) |
| Review packet (JSON + escaped Markdown + manifest) | Implemented and tested |
| Synthetic fixture adapter and 82-case evaluation (offline and `--execute`) | Implemented; **all fixtures are hand-written, no model is called** |
| Live model adapter (`claude-sonnet-5-5`, one request) and replay | Implemented and tested **offline with a fake client**; no real request has been made. A live call follows [docs/LIVE_CALL_PROCEDURE.md](docs/LIVE_CALL_PROCEDURE.md) (operator-controlled local Linux run) and needs an operator-approved packet; single use is enforced by a local ledger on that machine only, not globally. Live responses are never executed; a replay executes only with an exact-hash operator approval, and none exists |
| Docker executor for trusted synthetic checks | Implemented (`--execute`): byte-bound authorization, probe, per-container config checks, caps, timeout, verified cleanup. **Not verified for model-generated code**; see [docs/BOUNDARY.md](docs/BOUNDARY.md) |
| Trusted test execution (baseline and candidate) | The original model run stopped before execution. Codex separately evaluated its reviewed file; the agent replay gate remains closed. The built-in `--execute` path requires an approved replay or allowlisted synthetic fixture. |
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

## Local model demonstration

The Ollama adapter uses an installed `qwen3:4b` model on `127.0.0.1:11434`; no API key or
model API charge is needed. The demonstration is deliberately limited to one preselected return
statement in the synthetic `src/labels.py` issue. The model proposes a return statement; the
adapter inserts it into the pinned source and derives a unified diff. The raw model output,
model digest, actual prompt hash and transformation are recorded separately. This is not a
general-purpose issue resolver. Model output is untrusted and is never executed during generation.
The [local run record](docs/LOCAL_RUN.md) preserves the observed proposal, test-shaped prompt hints, failed attempts, and the separately supervised trusted-test evaluation.
A short [demo walkthrough](docs/DEMO.md) shows how to inspect the evidence.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export PYTHONDONTWRITEBYTECODE=1
.venv/bin/python -m ira build-demo --dest /tmp/ira-demo-repo
.venv/bin/python -m ira run --request demo/local/request.json --out /tmp/ira-local-output
```

The example pins the digest of the model used in the recorded local experiment. If your local
model has a different digest, create a request outside the checkout with the digest from Ollama's
`/api/tags`; do not silently substitute a different model. Each run makes one generation request;
different run IDs are separate attempts. Testing a real proposal requires a separate review of the
whole resulting file and explicit operator approval of its exact hashes. The existing Docker checks
do not establish safe isolation for arbitrary model-generated code.

## Pipeline

```
request (operator) -> trusted-test identity -> base verification -> prompt (issue + one file only)
  -> one adapter call -> strict JSON -> base-commit echo -> patch policy -> scratch apply + confirm
  -> offline: execution gate refuses -> packet
  -> --execute: byte-bound authorization -> probe -> baseline -> candidate (Docker) -> verdict -> packet
```

Terminal statuses are the eleven in contract item 7. Offline mode can emit `blocked_boundary_unverified`,
`adapter_error`, `rejected_schema`, `cannot_propose`, `rejected_policy`, `apply_failed` and `execution_error`. With
`--execute`, also `base_test_did_not_fail`, `timeout`, `tests_failed` and `tests_passed_awaiting_human_review`;
`execution_error` then also covers inconclusive container results (missing or malformed results, OOM, output cap,
crash), with the stage and a specific reason. `tests_failed` means the expected tests actually reported failures.

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

Trusted synthetic execution (Linux with a running Docker daemon; hand-written allowlisted fixtures only):

```bash
.venv/bin/python -m ira build-runner-image
```

```bash
.venv/bin/python -m ira run --execute --request demo/request.example.json --out out/exec-runs
```

```bash
.venv/bin/python -m ira eval --execute --out out/eval-exec
```

```bash
IRA_REQUIRE_DOCKER=1 .venv/bin/python -m pytest -q
```

Without `IRA_REQUIRE_DOCKER=1`, the Docker tests skip when Docker or the runner image is unavailable.

`run` writes `out/runs/<run_id>/{packet.json,review.md,manifest.json}` and refuses to reuse a run id. Check
`terminal_status` in its output; exit 0 alone says nothing about the patch.

## Layout

| Path | Purpose |
|---|---|
| `ira/gitcmd.py` | Git subprocess wrapper with a scrubbed environment and fixed subcommands |
| `ira/base.py` | Independent verification of the demo checkout against `demo/pinned.json` |
| `ira/models.py` | Strict JSON loader, request and response schemas |
| `ira/policy.py` | Diff grammar, exact-position application, caps (pure text processing) |
| `ira/scratch.py` | Scratch `git apply` and confirmation |
| `ira/prompt.py` | Prompt template (`ira.prompt.v1`) and its hash |
| `ira/adapters.py` | Synthetic fixture adapter |
| `ira/packet.py`, `ira/sanitize.py` | Review packet and escaping of untrusted text |
| `ira/runner.py`, `ira/evaluate.py`, `ira/cli.py` | Pipeline, evaluation, command line |
| `ira/authorize.py` | Byte-bound authorization of synthetic execution against `runner/synthetic_allowlist.json` |
| `ira/dockercmd.py`, `ira/execution.py` | Fixed-argv docker CLI; tree staging, container config checks, caps, timeout, cleanup |
| `ira/synthetic_exec.py`, `ira/verdict.py` | Probe, baseline and candidate orchestration; verdict and probe rules |
| `ira/imagebuild.py`, `runner/` | Pinned runner image inputs, in-container wrapper and probe |
| `demo/target/` | Source of the synthetic demo repository (built reproducibly; commit `eb455af5…`) |
| `demo/trusted_tests/` | Pinned issue tests; also used in the separately recorded manual evaluation |
| `fixtures/` | Synthetic responses generated by `tools/make_fixtures.py` (drift-checked by the tests) |

## Reuse and attribution

Patterns adapted (not copied wholesale) from the author's earlier repositories: text escaping from
`RyanJBush/Model-Regression-Detection-System` (`mrds/mdsafe.py`, commit `3086d87`), strict JSON loading from the same
repository, single-attempt and terminal-code structure from `RyanJBush/Autonomous-Coding-Agent-with-MCP-and-Multi-Agent-Collaboration` (`f956d12`), and injected-issue
evaluation cases from `RyanJBush/Prompt-Injection-Guardrails` (`7dddb8e`).

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Ryan J. Bush.
