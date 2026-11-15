# Supervised one-call procedure (operator-controlled, WSL Ubuntu 24.04)

This is the only supported way to make the one live model request. Its sha256 is bound into the approval packet, so
any edit to this file requires a newly prepared and newly approved packet.

**Why not GitHub Actions:** the repository has no GitHub Environments, `workflow_dispatch` requires the workflow on the
default branch, and required reviewers on a private repository depend on the account plan. None of it is set up.

**Why WSL Ubuntu 24.04:** the project is verified only on Linux (Ubuntu 24.04 locally and on hosted CI). The
operator's Windows machine has Docker Desktop, whose own WSL distributions are not for general use; a separate Ubuntu
distribution is the smallest setup that matches the verified environment. A throwaway Docker container is **not**
supported for this call: its single-use ledger would disappear with the container, and cloning the private repository
inside it adds a second credential to handle.

## What is enforced, and what is not

| Property | How | Scope |
|---|---|---|
| Exact approved request, prompt, base, model and parameters | `approval.verify` recomputes every binding before any network request | software |
| Approved code and procedure revision | bound commit; only `demo/live/approval.json` may differ; no modified, untracked or ignored files in the checkout | software |
| One request per run, to the real provider | one send, `max_retries=0`, no fallback; host pinned to `https://api.anthropic.com` | software |
| One use per approval | atomic `<approval_id>.spent` marker outside the checkout, claimed immediately before sending | **this machine and ledger only; not global** |
| No second attempt elsewhere, or after a lost ledger | this procedure | **operator discipline** |
| Spending | the packet's $0.25 is an **estimate threshold** checked before sending; only a Console spend limit is enforced by the provider | estimate vs provider-side |
| The response is never executed | the runner refuses live output for execution | software |

## Credential handling

- The key is entered **only** at the CLI's hidden prompt (`--api-key-prompt`, Python `getpass`, read from the terminal
  without echo). It is never typed on a command line, so it is not in shell history, command arguments or
  `/proc/<pid>/cmdline`, and it is never placed in an environment variable, so child processes and
  `/proc/<pid>/environ` do not see it. The CLI refuses to run if `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is set.
- **Remaining exposure:** the key is in that one Python process's memory until it exits (readable by root, or by the
  same user through a debugger, while it runs); it travels over TLS to the pinned host. If it was pasted, it may remain
  in the Windows clipboard or a password manager. Proxy variables (`HTTPS_PROXY`, `ALL_PROXY`) would route the TLS
  connection through that proxy; leave them unset unless it is a proxy you trust.
- **Cleanup:** run `ulimit -c 0` first so no core dump can contain it; clear the clipboard after pasting; after the run,
  delete or rotate the key in the Claude Console (a key created only for this call is the simplest).
- Never paste the key into chat, an issue, a file in the checkout, or a command line.

## Part A: set up and check the environment (no key, no cost)

1. In Windows PowerShell, list distributions. If only `docker-desktop` entries appear, install Ubuntu 24.04:

   ```powershell
   wsl --list --verbose
   ```

   ```powershell
   wsl --install -d Ubuntu-24.04
   ```

2. In the Ubuntu terminal, work in the Linux home directory (not under `/mnt/c`):

   ```bash
   sudo apt-get update && sudo apt-get install -y git python3.12-venv gh
   ```

   ```bash
   gh auth login
   ```

   ```bash
   gh repo clone RyanJBush/issue-resolution-agent ~/ira-live -- --branch m3-live-adapter
   ```

   ```bash
   cd ~/ira-live && git checkout <commit named in the handoff>
   ```

3. Create the venv **outside** the checkout, and keep bytecode out of the checkout for the whole session:

   ```bash
   python3 -m venv ~/.venvs/ira-live && ~/.venvs/ira-live/bin/pip install -r requirements-dev.txt
   ```

   ```bash
   export PYTHONDONTWRITEBYTECODE=1
   ```

   ```bash
   ~/.venvs/ira-live/bin/python -m ira build-demo --dest /tmp/ira-demo-repo
   ```

4. Run the environment check. It reads no key and makes no network request:

   ```bash
   ~/.venvs/ira-live/bin/python -m ira check-operator-env
   ```

   Every line must say `PASS` and the last line `READY`. Share that output for review. Do not continue to Part B
   until the packet for this exact commit has been reviewed and you decide to approve it.

## Part B: the one call (only after review and your explicit approval)

1. **Console:** set a workspace spend limit (recommended $1.00) on a non-default workspace and create an API key in it.
2. **Approve a copy outside the checkout:** copy `demo/live/approval.json` to `~/ira-approval.json`, read all of it,
   and fill in `operator_approval` (`approved: true`, `approved_by`, `approved_at`, `statement`). The committed
   proposal stays unapproved.
3. **Run once** (the key is requested at a hidden prompt after every other check passes):

   ```bash
   ulimit -c 0
   ```

   ```bash
   ~/.venvs/ira-live/bin/python -m ira run --live --api-key-prompt --request demo/live/request.json --approval-packet ~/ira-approval.json --out ~/ira-live-out
   ```

4. **Stop after one attempt, whatever happens.** A timeout, crash or unclear outcome has already consumed the
   approval. A second attempt needs a newly prepared packet (new `approval_id`) and fresh explicit approval.
5. **Clean up:** clear the clipboard; delete or rotate the key in the Console.
6. **Evidence to share:** `~/ira-live-out/live-sonnet55-001/` (packet, review, manifest), the recording under
   `~/ira-live-out/recordings/`, the marker under `~/.local/state/ira/live-approvals/`, the terminal output, and the
   Console usage entry for the request id. Never share the key.

## Limitations of this evidence

- The run is operator-reported: reviewers see the files the operator shares, not the machine.
- The single-use marker does not stop a run on another machine or after the ledger is deleted.
- Cost figures are estimates; the Console usage and invoice are authoritative.
- A live packet cannot reach a passing evaluation by design; that needs a separately reviewed and approved replay of
  the exact recorded bytes.
