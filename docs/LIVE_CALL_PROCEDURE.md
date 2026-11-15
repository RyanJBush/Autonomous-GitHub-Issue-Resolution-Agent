# Supervised one-call procedure (operator-controlled local Linux run)

This is the only supported way to make the one live model request. Its sha256 is bound into the approval packet, so
any edit to this file requires a newly prepared and newly approved packet.

**Why local, not GitHub Actions:** the repository has no GitHub Environments; `workflow_dispatch` requires the workflow
on the default branch; and required-reviewer protection for a private repository depends on the account plan. None of
that is set up or verified, so no workflow is provided.

## What is enforced, and what is not

| Property | How | Scope |
|---|---|---|
| The call uses exactly the approved request, prompt, base, model and parameters | `approval.verify` recomputes every binding before any network request | enforced in software |
| Code and procedure are the approved revision | bound commit; only `demo/live/approval.json` may differ; the checkout must have no modified, untracked or ignored files | enforced in software |
| One request per run | adapter sends once, `max_retries=0`, no fallback | enforced in software |
| One use per approval | atomic marker `<approval_id>.spent` created outside the checkout immediately before sending | **this machine and ledger directory only; not global** |
| No second attempt elsewhere or after a lost ledger | this procedure | **operator discipline, not software** |
| Spending | estimate and request cap in the packet; Console spend limit | the estimate is **not** a bound; only a Console spend limit is provider-side |
| The response is never executed | runner refuses `live_model` output for execution | enforced in software |

## Steps

1. **Console spend limit.** In the Claude Console, set a workspace spend limit (recommended $1.00) on a non-default
   workspace, and create an API key in that workspace. Do not put the key in any file in the checkout.
2. **Fresh clone, nothing generated inside it.**

   ```bash
   git clone https://github.com/RyanJBush/issue-resolution-agent ~/ira-live && cd ~/ira-live
   ```

   ```bash
   git checkout <commit that contains the committed proposal>
   ```

   ```bash
   python3 -m venv ~/.venvs/ira-live && ~/.venvs/ira-live/bin/pip install -r requirements-dev.txt
   ```

   ```bash
   PYTHONDONTWRITEBYTECODE=1 ~/.venvs/ira-live/bin/python -m ira build-demo --dest /tmp/ira-demo-repo
   ```

3. **Approve a copy, outside the checkout.** Copy `demo/live/approval.json` to `~/ira-approval.json`, read all of it,
   and fill in `operator_approval` (`approved: true`, `approved_by`, `approved_at`, `statement`). Leave the committed
   proposal unapproved.
4. **One run.** Export the key only for this command, and write outputs outside the checkout:

   ```bash
   ANTHROPIC_API_KEY='<key>' PYTHONDONTWRITEBYTECODE=1 ~/.venvs/ira-live/bin/python -m ira run --live --request demo/live/request.json --approval-packet ~/ira-approval.json --out ~/ira-live-out
   ```

5. **Stop after one attempt, whatever happens.** If the run times out, crashes, or its outcome is unclear, do not
   retry: the marker has already consumed the approval. A second attempt requires a newly prepared packet (new
   `approval_id`) and fresh explicit approval.
6. **Evidence to share for review:** `~/ira-live-out/live-sonnet55-001/` (packet, review, manifest), the recording
   under `~/ira-live-out/recordings/`, the marker under `~/.local/state/ira/live-approvals/`, the terminal output, and
   the Console usage entry for the request id. Never share the key.

## Limitations of this evidence

- The run is operator-reported: Codex and Claude see the files the operator shares, not the machine.
- The single-use marker does not stop a run on another machine or after the ledger is deleted.
- The packet's cost figures are estimates; the Console usage and invoice are authoritative.
- A passing live packet is impossible by design: live output is never executed. Evaluation needs a separately
  reviewed and approved replay of the exact recorded bytes.
