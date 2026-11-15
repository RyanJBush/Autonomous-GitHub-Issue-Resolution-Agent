# Milestone 3 design: one real model adapter and a reviewable model-generated proposal

Status: **offline implementation complete on `m3-live-adapter`; no model has been called.** Base: accepted M2 commit
`f224d8e6c0e13b4c38db775be9f76c738ab3fdb1`.

## Codex amendments (applied; they supersede the text below where it differs)

1. **Cost is an estimate, not a bound.** `ira/pricing.py` labels every figure `ESTIMATE`. Output uses `max_tokens`,
   which the provider documents as a strict limit that includes thinking tokens (billed as output). Input is a
   heuristic (prompt bytes + 64 framing tokens). The provider's token-counting endpoint is free but needs an API key,
   so it is not used before approval. Independent control: an organization or workspace spend limit in the Claude
   Console (not settable on the default workspace); a reached self-set limit returns HTTP 400, the tier cap HTTP 429.
2. **Approval is a person's recorded decision, not a digest.** `ira prepare-live-approval` writes a packet binding the
   request, prompt, base, model and parameters, estimate, cap, code commit and workflow revision, with
   `operator_approval.approved: false`. `ira run --live` refuses unless `approval.verify` recomputes every binding and
   finds the operator's approval.
3. **First call defaults to `claude-sonnet-5-5`** ($2 / $10 per MTok, verified on the published pricing page
   2026-11-15), `max_tokens` 8,000, effort `medium`, 120 s timeout, cap $0.25. Access and credits are not assumed.
4. **GitHub Environment gate is conditional.** `.github/workflows/live-proposal.yml` is prepared for review only: no
   secret exists, it has not been dispatched, and the gate is not claimed to work until observed.
5. **Replay authorization binds exact hashes** (recording, response, patch, resulting file, base, live run id) plus an
   operator approval that states the whole resulting file was reviewed. Packets include the whole resulting file.
   `runner/reviewed_model_outputs.json` is empty; no replay is enabled.
6. **Honest outcome.** Every packet has `end_to_end_objective`; a live run is always `not_met` (never executed), and only
   an approved replay that passes the trusted tests reaches `evaluated_awaiting_human_review`.
7. **Offline tests only**, enforced by an autouse fixture that blocks IPv4/IPv6 connections.

## Second review round (Codex findings at 9caa90f; applied)

1. **No provider text in artifacts.** Exception messages and bodies are never stored. The packet keeps the exception
   class, status code, and only allowlisted provider error types and codes.
2. **Single use is local, not global.** An atomic `<approval_id>.spent` marker is created outside the checkout
   immediately before the request is sent (`ira/single_use.py`). Concurrent and repeated attempts on that machine and
   ledger cannot both proceed, and a timeout consumes the approval. Other machines and a deleted ledger are covered
   only by the supervised procedure, which requires a new packet and fresh approval for any second attempt.
3. **No GitHub Actions path.** The repository has no Environments, `workflow_dispatch` needs the default branch, and
   required reviewers on a private repository depend on the plan. The workflow was removed. The one call follows
   `docs/LIVE_CALL_PROCEDURE.md` (operator-controlled local Linux run), whose sha256 the packet binds.
4. **Clean tree fails closed** on modified, staged, untracked and ignored files, so the venv and all outputs live
   outside the checkout; `--out` inside the checkout is refused for live runs.

The sections below are the reviewed design. Where they say "Opus", "deterministic", "bound", "exactly one request",
"GitHub Actions" or "workflow", read the amendments above: sections 2 (option A) and 4 are superseded.

## 0. Summary

1. Add one live adapter (Anthropic Messages API, one request, no tools, no retries, no fallback) and one replay adapter
   that re-reads a recorded live response by hash. The fixture adapter is unchanged.
2. Make exactly one approved live call. Its packet is produced by the existing pipeline. The live response is
   **never executed**: the M2 gate refuses it (`execution_not_authorized:untrusted_execution_not_enabled`).
3. Close the remaining gap with a **review gate**, not a stronger sandbox claim: the operator and Codex inspect the
   exact recorded bytes; if approved, those bytes (by hash) become eligible for the same trusted-check execution M2
   already accepted for hand-reviewed synthetic code, and a replay run produces the evaluated packet.

What this does not do: it does not verify that unreviewed model-generated code is safely isolated.

## 1. Run kinds and labels (kept distinct end to end)

| `run_kind` | Source of response bytes | Can reach the executor | Packet label |
|---|---|---|---|
| `synthetic_fixture` | hand-written file in `fixtures/responses/` | only if listed in `runner/synthetic_allowlist.json` | `SYNTHETIC: hand-written fixture output; no model was called` (unchanged) |
| `live_model` | one Messages API call made during this run | **never** | `LIVE MODEL RUN` |
| `replay_recording` | `recordings/<run_id>.json` written by a live run, verified by hash | only if its exact hashes are in `runner/reviewed_model_outputs.json` (section 5) | `REPLAY of a recorded live model response` |

## 2. Model access options (observed state, then recommendation)

Observed in this session (2026-11-15, no API calls made): `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
`ANTHROPIC_PROFILE` and the workload-identity variables are unset; the `ant` CLI is not installed; there is no
profile directory. The `anthropic` SDK is not installed in the venv (PyPI reports `2.8.0` as current). So **no model
access path is verified.**

| Option | How the key is held | Pros | Cons |
|---|---|---|---|
| **A. GitHub Actions `workflow_dispatch` job (recommended)** | Repository secret `ANTHROPIC_API_KEY`, scoped to a GitHub Environment `live-model` that requires the operator's approval | Key never enters this VM (which holds other credentials) or chat; each run has an auditable run id; approval gate in GitHub as well as in code | Operator adds the secret and environment; one more workflow job to review |
| B. Operator's own Linux machine | Operator's local key or `ant auth login` profile | Simplest; no repository secret | Evidence is a local packet only; harder for Codex to verify independently |
| C. This cloud session | Would need a key in the environment | — | Not recommended: `api.anthropic.com` is on this proxy's `NO_PROXY` list, so proxy credential injection would not apply, and a plain environment variable is stored in plain text |
| D. Amazon Bedrock with this VM's AWS keys | Existing keys of unknown scope | — | Not recommended: unknown scope and billing, not operator-approved |

Model: **`claude-opus-5-5`** (current default Opus). Cheaper alternatives (`claude-sonnet-5-5` at $2 / $10 per MTok,
`claude-haiku-5-5` at $0.10 / $0.50) are the operator's choice; the adapter pins whichever is approved.

## 3. Adapter contract

**Request schema change** (`ira.request.v1`, additive): `adapter.kind` accepts `live_model` and `replay_recording`.

```
live_model:       {kind, model: one of a trusted allowlist (initially ["claude-opus-5-5"]),
                   max_tokens <= 16000, effort in {"low","medium","high"}, timeout_s <= 300,
                   cost_cap_usd <= 0.50}
replay_recording: {kind, recording_path, recording_sha256}
```

**Call (live adapter, `ira/adapters.py`):** official `anthropic` Python SDK, pinned exact version.

```
client = anthropic.Anthropic(max_retries=0, timeout=timeout_s)      # one request; no SDK retries
client.messages.create(model=..., max_tokens=..., output_config={"effort": ...},
                       messages=[{"role": "user", "content": prompt}])  # no tools, no system, no fallbacks
```

- Thinking: omitted (adaptive is always on for this model and cannot be disabled; effort is the control, set explicitly).
- No `fallbacks`: Anthropic's guidance is to enable server-side fallback by default for this model. I propose **not**
  enabling it here, because a fallback silently changes which model produced the proposal, and contract item 6 requires
  one recorded model identity. A refusal becomes `adapter_error` / `model_refusal:<category>`. **Decision for the
  operator and Codex.**
- No structured-output constraint in v1: the strict parser is the contract; a malformed response is a legitimate
  `rejected_schema` outcome and an evaluation data point.

**Approval binding (before any network call):** the CLI requires `--live --approve-request <sha256 of the request
file>` (the SwarmOS digest-approval pattern). Without an exact match the adapter is never constructed.

**Cost guard (deterministic, before the call):** worst-case cost = `(prompt_bytes + 100) x input_price +
max_tokens x output_price`. Prompt bytes upper-bound input tokens because no token is shorter than one byte. If the
bound exceeds `cost_cap_usd`, the run stops with `adapter_error` / `cost_cap_exceeded` and no call. Prices come from a
pinned table that records its source and date; they are labeled estimates.

Worked example with the current demo prompt (2,008 bytes, measured) and `claude-opus-5-5` at $4 / $20 per MTok:
input ≤ 2,108 tokens → ≤ $0.0084; output ≤ 16,000 tokens (thinking included) → ≤ $0.32; **bound ≤ $0.33 per call.**
Prices are from the API reference cached 2026-11-03 and must be re-checked before approval.

**Response handling:**

| Observation | Result |
|---|---|
| `stop_reason == "end_turn"` and exactly one non-empty `text` block | raw bytes = that text (UTF-8), passed to the existing strict parser |
| `stop_reason == "refusal"` | `adapter_error` / `model_refusal:<stop_details.category>` |
| `stop_reason == "max_tokens"` | `adapter_error` / `output_truncated` |
| zero or several text blocks, other stop reasons | `adapter_error` / specific reason |
| typed SDK errors (auth, rate limit, 4xx, 5xx, connection, timeout) | `adapter_error` / error class name; no retry |

Thinking blocks (empty under the default display) are counted, not stored.

**Recorded in the packet and the recording (never the key or request headers):** requested model and `response.model`,
request id, SDK version, adapter version, `stop_reason`, `usage` (input, output, cache read and cache creation tokens),
the estimated cost from usage and the pinned price table, request limits, effort, prompt-template version and hash,
prompt hash and bytes, and the bounded raw response (≤ 32,768 bytes) with its hash. The recording also stores the base
commit and the live packet's run id. A replay must reproduce the same prompt hash, so a recording cannot be replayed
against a different prompt, issue or base.

## 4. Where the live call runs (recommended option A)

New job `live-proposal` in a separate workflow file, `on: workflow_dispatch` only, `environment: live-model` (required
reviewer: operator), `permissions: contents: read`, no Docker steps. It runs `ira run --live --approve-request <sha>`
with `--execute` **not** set, uploads the packet and the recording as artifacts, and fails if any container is created.
The executor's own rule still refuses `live_model`, so the job cannot run candidate code even if misconfigured.

## 5. The execution-boundary decision

**Current state.** M2 accepted Docker + probes + per-container config checks only as a boundary for **trusted
synthetic checks**: code personally inspected, with understood effects, bound by exact bytes. Codex has ruled that
neither these probes nor `permissions` settings prove safe isolation of arbitrary model-generated code (shared kernel,
rootful daemon, job token in runner processes, runner user in the `docker` group, in-process tampering).

**What is blocked.** Evaluating the real proposal against the trusted tests requires executing model-generated code.
Under the current rules that is not permitted, so a live packet ends at `blocked_boundary_unverified` /
`execution_not_authorized:untrusted_execution_not_enabled`.

**Options:**

| Option | What it establishes | Effort | Recommendation |
|---|---|---|---|
| **1. Review gate: promote exact reviewed bytes** | The specific proposal is treated like a synthetic fixture: inspected by people, effects understood, bound by hash. Execution then rests on the trust basis M2 already accepted | Small: one allowlist file, one static pre-screen, one approval record, replay mode | **Recommended for this MVP** |
| 2. Stronger runtime (gVisor `runsc`, or a microVM) on the hosted runner | Reduces kernel attack surface; still not proof | Medium to large; daemon reconfiguration on the runner, new probes | Later, if unreviewed execution becomes a goal |
| 3. No execution (static review only) | Nothing against trusted tests | None | Fails the project goal |

**Option 1 in detail:**

1. The live run produces packet L (blocked) and recording R.
2. **Static pre-screen (trusted, no execution):** parse the policy-computed new file content with `ast.parse` (parsing,
   not compiling or importing). Refuse promotion if it adds any `import`/`from` statement, or calls or names `open`,
   `exec`, `eval`, `compile`, `__import__`, `getattr`, `globals`, `os`, `sys`, `subprocess`, `socket`, `builtins` or
   dunder attributes, or adds module-level statements other than definitions and docstrings. This is a filter in front
   of the human review, not a substitute for it.
3. **Human review:** the operator reads the bounded diff in `review.md`; Codex reviews independently.
4. **Approval record:** `runner/reviewed_model_outputs.json` (its hash pinned in `demo/pinned.json`) gains one entry
   with `recording_sha256`, `response_sha256`, `patch_sha256`, `new_content_sha256`, base commit, model, the live run
   id, reviewer names, date and a one-line understood effect. Authorization for `replay_recording` requires an exact
   match on every hash. `live_model` stays refused unconditionally.
5. **Replay run** on the hosted `boundary-synthetic` runner with `--execute`: same probe, config checks, baseline and
   candidate as M2. Packet P carries `run_kind=replay_recording`, links to L by run id and recording hash, and states
   `verified_for_model_generated_code: false` and `executed_because: human_review_approval`.

If the reviewed proposal is rejected (by the pre-screen or the reviewers), packet L is the honest end state, and the
README says the proposal was not executed and why.

## 6. Verification plan

**Offline (no network, no key), in CI:**
- A fake SDK client is injected into the live adapter: success; refusal with category; `max_tokens`; zero or several
  text blocks; oversized text; each typed SDK error. Each maps to the specified status and reason, with exactly one
  client call.
- Approval: a missing or mismatched `--approve-request` digest means the adapter is never constructed (zero client
  calls). The cost guard refuses with zero calls when the bound exceeds the cap.
- No secret in the output: a fake key planted in the environment never appears in packet, recording, review or trace.
- Replay: hash mismatch, prompt-hash mismatch and a tampered recording are refused; replay of an unreviewed recording
  reaches zero `docker create` calls; a reviewed recording with one changed byte is refused.
- Pre-screen: unit cases for each forbidden construct; the known-good synthetic fix passes.
- Labels: each run kind's packet carries its own label and `run_kind`; nothing synthetic is labeled live.

**One live call (only after the operator approves the model, cost cap and access option):**
- Expected: `live-proposal` job run id observed, one request id, usage recorded, cost estimate ≤ cap, packet L valid,
  zero containers created. Any status is acceptable evidence (`rejected_schema` and `rejected_policy` included),
  provided it is reported as observed.

**Replay execution (only after review approval):** hosted `boundary-synthetic` job runs the replay with `--execute`;
observed baseline failures, candidate outcome and cleanup are reported from the job log and packet P.

## 7. Files, effort, stopping points

**New:** `ira/live_adapter.py` (or a section of `ira/adapters.py`), `ira/replay.py`, `ira/prescreen.py`,
`ira/pricing.py` (pinned price table with source and date), `runner/reviewed_model_outputs.json` (empty until an
approval), `.github/workflows/live-proposal.yml`, `tests/test_live_adapter.py`, `tests/test_replay_and_review.py`,
`tests/test_prescreen.py`.
**Changed:** `ira/models.py` (adapter kinds), `ira/authorize.py` (reviewed-output path for replay only),
`ira/runner.py`, `ira/cli.py` (`--live`, `--approve-request`, replay), `ira/packet.py` (labels, cost and usage
section), `requirements-dev.txt` (pinned `anthropic`), `README.md`, `docs/BOUNDARY.md`, `docs/EFFORT.md`.

**Estimate (nominal, not calibrated to assistant time):** offline adapter, replay, pre-screen and tests about 2–3 h;
live job and one call about 0.5 h; review gate and replay execution about 0.5–1 h; README and demo about 1 h.

**Stopping points:**
1. After the offline implementation and tests, before any call: handoff to Codex.
2. After the single live call: handoff with packet L.
3. After the review decision: replay packet P, or a documented non-execution.

Draft PR creation still needs the operator's explicit approval; there is never an automatic merge.

## 8. Decisions requested

**From the operator:** the model (recommended `claude-opus-5-5`), the per-call cap (proposed $0.50) and total M3
budget (proposed $1.50, at most three calls), the access option (recommended A), and whether to set an independent
spend limit in the Anthropic Console.

**From Codex:** option 1 (review gate) as the boundary decision for this MVP; fallbacks disabled; no structured-output
constraint in v1; refusal, truncation and API errors mapped to `adapter_error`; the pre-screen rule list.
