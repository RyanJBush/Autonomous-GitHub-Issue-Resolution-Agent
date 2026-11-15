# Milestone 2 design: baseline/candidate test runner and execution-boundary evidence

Status: **proposal for Codex review. Not implemented.** No candidate code has been executed for M2.
Base: accepted M1 commit `78ef8c71b09b88e20342cfb3832b5b5f83c9d72c`, branch `m2-execution`.

Scope rule enforced in code, not only in docs: in M2 the executor runs **only** `run_kind=synthetic_fixture`
requests whose fixture case is on a hand-reviewed allowlist. Live or replayed model output cannot reach the executor in
M2 under any boundary result. Results are labeled **trusted synthetic checks**.

## 1. Execution environment, trust boundary, residual risks

### Components

| Component | Local development (this VM) | Hosted evidence (GitHub Actions) |
|---|---|---|
| Host | Firecracker VM, Ubuntu 24.04, kernel 6.18, cgroup v1; root; restarted 03:19:48Z today, so dockerd state did not persist | `ubuntu-24.04` GitHub-hosted runner, ephemeral VM (Docker presence and cgroup version **to be observed**, not assumed) |
| Credentials on host | Present: 13 env names match token/secret/key, including AWS keys, `GH_TOKEN`, a messaging token | Job token only; job runs with `permissions: {}` and no secrets referenced |
| Container runtime | Docker 29.9.2, rootful dockerd started manually, runc, default seccomp | Runner's preinstalled Docker (version recorded) |
| Image | `python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b` plus pytest installed at build time from a hash-pinned lock | same |
| Use in M2 | Develop the runner with trusted synthetic fixtures (Codex decision) | Boundary evidence job: probe + trusted synthetic fixtures |

### Trust boundary

```
TRUSTED (host process, trusted code)              UNTRUSTED (inside one container per run)
- ira runner, policy, verdict parser              - candidate tree (patched src/labels.py)
- builds base and candidate trees from git objects - everything the pytest process does after it
- stages pinned trusted tests + runner config        imports src.labels
- docker CLI with a fixed argv                    - stdout/stderr and the JUnit file it produces
- reads bounded output after the container exits
```

The container receives two read-only bind mounts (the materialized tree and the staged trusted directory), a size-bounded
tmpfs at `/tmp`, no network, no capabilities, a non-root uid and no environment variables except a fixed list.

### Residual risks (stated in the packet and BOUNDARY.md)

1. **Shared kernel.** A kernel, runc or dockerd escape reaches the host. Locally that exposes the VM's credentials,
   which is why the local VM is **not** acceptable for model output (Codex decision stands). On the hosted runner it
   reaches an ephemeral VM holding a job token with no permissions.
2. **Rootful daemon, no user-namespace remapping.** uid 65534 in the container is uid 65534 on the host.
3. **In-process tampering.** Candidate code runs inside the pytest process (and as the same uid as the in-container
   wrapper), so it can forge exit codes, JUnit XML or stdout. A pass is evidence for human review only.
4. **Resource exhaustion of the host** is bounded (CPU, memory, pids, tmpfs, file size, output bytes, wall clock) but
   not eliminated; these limits are tested with synthetic fixtures, not proven complete.
5. **Supply chain.** Base image pinned by digest; pytest and its dependencies pinned by hash. Docker Hub and PyPI are
   contacted at image build time only, never during a candidate run.
6. **GitHub-hosted runner specifics** (Docker version, cgroup version, preinstalled daemon configuration) are outside
   our control and are recorded per run, not assumed.

## 2. How each property is checked

Each run first starts a **probe container** with exactly the candidate run's flags and mounts, running a trusted probe
script (`/trusted/probe.py`, never candidate code). The run may execute only if every host-side and in-container check
passes; otherwise `blocked_boundary_unverified` with the failing check named. The probe report goes into the packet.

| Property | Host-side check (`docker inspect` of the created container, before start) | In-container check (probe) |
|---|---|---|
| No credentials via environment | `Config.Env` equals the expected list exactly (image ENV + `PYTEST_DISABLE_PLUGIN_AUTOLOAD`, `IRA_ROLE`) | env names subset of the allowlist; a random canary planted in the **docker client's** environment is absent |
| No tokens via files or mounts | `HostConfig.Binds` equals exactly the two expected `:ro` binds; `Mounts` has no other entries; `Devices` empty; `Privileged` false | `/proc/self/mountinfo` lists only `/`, `/tmp` (tmpfs), `/work/repo` (ro), `/trusted` (ro) plus standard `/proc`, `/sys`, `/dev`; no `docker.sock` anywhere; no `.git` in `/work/repo`; `/root`, `~/.aws`, `~/.docker`, `~/.config/gh` absent or unreadable |
| Restricted network | `NetworkMode == "none"`, no `PortBindings`, no `ExtraHosts`, `Dns` empty | interfaces = `lo` only; DNS lookup of `pypi.org` fails; TCP connect to `1.1.1.1:443`, `169.254.169.254:80` and the default Docker bridge gateway fails |
| Least privilege | `CapDrop == ["ALL"]`, `CapAdd` empty, `SecurityOpt` contains `no-new-privileges`, `ReadonlyRootfs` true, `User == "65534:65534"` | `CapEff` = 0, `NoNewPrivs` = 1, `Seccomp` = 2, uid/gid 65534; writes to `/`, `/work/repo`, `/trusted` fail |
| Bounded resources | `Memory`, `MemorySwap`, `PidsLimit`, `NanoCpus`, `Ulimits` (`fsize`, `nofile`) equal configured values | cgroup memory and pids limits read back equal the configured values; `ulimit -f` matches |
| Bounded output | `LogConfig.Type == "none"` (no host log file growth) | — (host reads stdout/stderr through the attached stream with a byte cap and kills the container when exceeded; to be verified that attach works with logging disabled) |
| Timeout cleanup | containers are named `ira-<run_id>-<role>-<nonce>` and labeled `ira.run=<run_id>` | host deadline expiry → `docker kill` → `docker rm -f` → `docker ps -a --filter label=ira.run=<id>` must be empty, all within a grace period; failure to clean up is `execution_error` |

Container flags (fixed; values from the request's `execution` limits, capped):

```
docker create --name ira-<id>-<role>-<nonce> --label ira.run=<id> --label ira.role=<role>
  --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m
  --memory <m>m --memory-swap <m>m --pids-limit <p> --cpus <c>
  --cap-drop ALL --security-opt no-new-privileges --user 65534:65534
  --ulimit fsize=33554432 --ulimit nofile=256 --log-driver none --init
  --env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 --env IRA_ROLE=<role>
  -v <tree>:/work/repo:ro -v <trusted>:/trusted:ro -w /trusted
  <image@digest> python -I -B /trusted/run_tests.py
```

Docker is invoked through a second fixed-argv wrapper (like `gitcmd.py`) with an allowlist of subcommands
(`version`, `info`, `image inspect`, `create`, `inspect`, `start`, `wait`, `kill`, `rm`, `ps`; `build` only from the
separate `ira build-runner-image` command, never on the candidate path), a scrubbed environment and output caps.
Host trees are materialized from git objects (`ls-tree -r`, `cat-file blob`), files `0444`, directories `0555`, under
a `0755` scratch root so uid 65534 can read them; the candidate tree is the base tree with only the allowlisted file
replaced by the policy-computed, scratch-confirmed bytes.

## 3. Trusted test command, test identity, verdict rules

**Identity (pinned in `demo/pinned.json`, outside candidate control).** `demo/trusted_tests/test_issue.py`
(sha256 `dbc96774…`), plus new pinned files `runner/run_tests.py`, `runner/pytest.ini`, `runner/probe.py`,
`runner/requirements.lock`, `runner/Dockerfile`. Hashes are checked before staging; the image digest is recorded.

**Command (fixed).** In-container wrapper `python -I -B /trusted/run_tests.py` starts one child. `-I` implies `-E`, so
`PYTHON*` variables are ignored by design; `-B` (not `PYTHONDONTWRITEBYTECODE`) prevents bytecode writes. Pytest's own
`PYTEST_DISABLE_PLUGIN_AUTOLOAD` is still read because it is not a `PYTHON*` variable:

```
python -I -B -c "<fixed bootstrap: sys.path.insert(0, '/work/repo'); raise SystemExit(pytest.main(ARGS))>"
ARGS = ["-c", "/trusted/pytest.ini", "--rootdir", "/trusted", "--confcutdir", "/trusted",
        "-p", "no:cacheprovider", "-q", "-rA", "--junitxml", "/tmp/ira/junit.xml", "/trusted/test_issue.py"]
```

The wrapper then prints one bounded JSON envelope (pytest exit code, JUnit XML up to 256 KiB) to stdout. The child's
own output goes to stderr. `pytest.ini` sets empty `addopts` and fixes `junit_family`. The candidate's `pyproject.toml`
is never read as pytest configuration. Same image, flags, wrapper and timeout for baseline and candidate; only the
`/work/repo` contents differ.

**Expected IDs:** `test_issue.py::test_trims_and_collapses_whitespace`, `::test_whitespace_only_label_is_invalid`,
`::test_existing_behaviour_kept`. **Expected base failures:** the first two.

**Verdict rules** (host parses the envelope and JUnit; XML with `DOCTYPE` or `ENTITY` declarations is rejected before
parsing). Order of evaluation:

| Step | Condition | Terminal status / reason |
|---|---|---|
| Gate | boundary probe or inspect check fails | `blocked_boundary_unverified` / `<check>` |
| Gate | request is not a reviewed synthetic fixture (M2 rule) | `blocked_boundary_unverified` / `untrusted_execution_not_enabled` |
| Baseline | docker could not create/start, image missing, cleanup failed | `execution_error` / `baseline_infrastructure:<detail>` |
| Baseline | host deadline expired | `timeout` / `baseline_timeout` |
| Baseline | envelope or JUnit missing/malformed/oversized, pytest exit not 0 or 1, IDs differ from expected, any skip or error | `execution_error` / `baseline_unexpected:<detail>` (base code is trusted, so an anomaly means our setup is wrong) |
| Baseline | any expected base failure passed | `base_test_did_not_fail` / `<test id>` |
| Baseline | non-expected test failed | `execution_error` / `baseline_unexpected:other_failure` |
| Candidate | docker could not create/start, cleanup failed | `execution_error` / `candidate_infrastructure:<detail>` |
| Candidate | host deadline expired | `timeout` / `candidate_timeout` |
| Candidate | container started but OOM-killed, output cap exceeded, envelope/JUnit missing or malformed, pytest exit not 0, IDs differ, any skip/error/failure | `tests_failed` / specific reason (anomalies caused inside the container are attributed to the candidate) |
| Candidate | exit 0, exactly the expected IDs, all passed, 0 skipped, 0 errors | `tests_passed_awaiting_human_review` / `all_expected_tests_passed` |

Each run records: fixed argv, image digest, container name and id, pytest exit code, `State.OOMKilled`,
`State.ExitCode`, duration, timed-out flag, cleanup result, stdout/stderr (16 KiB each, truncation flagged), and parsed
per-test outcomes. `tests.baseline` and `tests.candidate` stop being null when they run.

## 4. Synthetic cases and acceptance checks

All are hand-written, inspected fixtures with understood effects; each still passes M1 policy.

| Case | What the candidate does | Expected M2 status | Evidence it provides |
|---|---|---|---|
| `good_trim_collapse` (existing) | correct fix | `tests_passed_awaiting_human_review` | base fails exactly the 2 expected tests; candidate passes all 3 |
| `known_bad_strip_only` (existing) | wrong fix | `tests_failed` | collapse test fails |
| `known_bad_comment_insertion` (existing) | no fix | `tests_failed` | — |
| `known_bad_execution_canary` (existing) | writes `/tmp/ira-m1-execution-canary` at import | `tests_failed` | host canary path absent afterwards (file effects stay in the container tmpfs) |
| `m2_sleep_timeout` (new) | `time.sleep(3600)` at import | `timeout` / `candidate_timeout` | wall time ≤ timeout + grace; container gone (`docker ps -a` by label empty) |
| `m2_memory_hog` (new) | allocates 1 GiB at import | `tests_failed` / `oom_killed` | `State.OOMKilled` true |
| `m2_fork_burst` (new) | tries to spawn 200 processes, then applies the fix | `tests_passed_awaiting_human_review` or `tests_failed` (outcome recorded, not predicted) | spawn count capped below the pids limit; host unaffected |
| `m2_output_flood` (new) | writes 10 MB to stdout at import, then the fix | `tests_failed` / `output_limit_exceeded` | host stops reading at the cap and kills the container |
| `m2_network_canary` (new) | correct fix, but raises at import if DNS or any TCP connect succeeds | `tests_passed_awaiting_human_review` | a pass is (candidate-influenced) evidence the network was closed |
| `m2_env_canary` (new) | correct fix, but raises if any env name matches token/key/secret/AWS/GH/GITHUB/ANTHROPIC or the client canary | `tests_passed_awaiting_human_review` | same, for environment |

Plus fault-injection tests (pytest, not fixtures): unreachable Docker daemon → `execution_error`; alternate pinned
spec expecting `test_existing_behaviour_kept` to fail on base → `base_test_did_not_fail`; tampered staged trusted file
→ refused before staging; probe check forced to fail → `blocked_boundary_unverified`; live/replay request →
`untrusted_execution_not_enabled` with zero `docker create` calls.

**Observable acceptance checks for M2:**

1. Offline suite still passes without Docker; Docker tests are marked and **fail (not skip)** when
   `IRA_REQUIRE_DOCKER=1` is set and Docker is unavailable.
2. Probe report shows every check in section 2 passing on the local VM and on the hosted runner, with the host facts
   recorded (Docker version, cgroup version, credential-named host env count, names only).
3. `good_trim_collapse`: baseline JUnit shows exactly the 2 expected failures and 1 pass; candidate shows 3 passes;
   status `tests_passed_awaiting_human_review`.
4. Every case in section 4 reaches its expected status locally and in the hosted job; `expected_m2_status`
   predictions that turn out wrong are reported, not silently edited.
5. Timeout case: measured wall time and an empty `docker ps -a --filter label=ira.run=…` after cleanup, both in the
   packet and in the hosted log.
6. No-execution test extended: the only executables are `git` and `docker`, each with its fixed subcommand list;
   live/replay requests produce zero `docker create` calls.
7. A hosted run of the boundary job is **observed** (run id, head SHA, step results, counts) before any claim.
8. Packet invariant: baseline/candidate fields populated or null with a reason for every status.

## 5. Files, effort, stopping point

**New:** `ira/dockercmd.py` (fixed-argv docker wrapper), `ira/execution.py` (materialize trees, create/start/wait/
inspect/kill/rm, output caps), `ira/verdict.py` (envelope + JUnit parsing, rules above), `runner/Dockerfile`,
`runner/requirements.lock`, `runner/pytest.ini`, `runner/run_tests.py`, `runner/probe.py`, `docs/BOUNDARY.md`,
`tests/test_verdict.py`, `tests/test_dockercmd.py`, `tests/test_execution_docker.py`.
**Changed:** `ira/boundary.py` (real probe), `ira/runner.py` (baseline/candidate stages), `ira/packet.py`,
`ira/cli.py` (`build-runner-image`, `eval --execute`), `demo/pinned.json` (runner file hashes),
`tools/make_fixtures.py` + new fixtures, `tests/test_no_execution.py`, `.github/workflows/ci.yml` (second job
`boundary-synthetic`, `permissions: {}`, `needs: offline`), `README.md`, `docs/EFFORT.md`.

**Effort estimate:** about 4–5 h nominal, above the earlier 3 h, because of the probe and the hosted job. Estimates have
not been calibrated against assistant time: M1 was estimated at 4 h and measured 23.9 min of session wall-clock time.

**Stopping point:** acceptance checks 1–8 observed locally and in one hosted run for a stated commit; handoff to Codex.
No model call, no live or replayed output executed, no PR. M3 (real adapter) does not start without Codex's acceptance
and the operator's approval of any paid call.

## Decisions requested from Codex

1. Attribute in-container anomalies during the **candidate** run (no JUnit, OOM, output cap, crash) to `tests_failed`
   with a specific reason, and the same anomalies during the **baseline** run to `execution_error`.
2. Accept the hosted runner + Docker + probe as the M2 evidence target, with the local VM limited to trusted synthetic
   development checks.
3. Accept the M2 code rule that only allowlisted synthetic fixtures can reach the executor.
