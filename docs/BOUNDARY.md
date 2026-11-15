# Execution boundary: evidence and residual risks (Milestone 2)

**Scope.** This boundary is used only for **trusted synthetic checks**: hand-written fixtures, each bound by exact bytes
to a hand-reviewed allowlist (`runner/synthetic_allowlist.json`, hash pinned in `demo/pinned.json`). Live or replayed
model output never reaches the executor. Nothing here verifies that arbitrary model-generated code is safely isolated;
every packet records `verified_for_model_generated_code: false`.

## What runs where

```
host (trusted): ira runner -> authorize (exact bytes) -> docker CLI (fixed argv, scrubbed env, empty DOCKER_CONFIG)
  per run: probe container -> baseline container -> candidate container   (one image, one flag set, one command)
container (untrusted after the candidate is imported):
  /work/repo  read-only bind of a tree materialized from git objects (base, or base + verified patch result)
  /trusted    read-only bind of pinned files: test_issue.py, pytest.ini, run_tests.py, probe.py
  /tmp        tmpfs 64 MiB, noexec
  no network, no capabilities, uid 65534, private cgroup and IPC namespaces, logging driver none
```

Command: `python -I -B /trusted/run_tests.py`, which runs pytest with `-c /trusted/pytest.ini --rootdir /trusted
--confcutdir /trusted -p no:cacheprovider --junitxml /tmp/ira/junit.xml /trusted/test_issue.py` in a child process
and prints one JSON envelope.

## Checks performed for every run

1. **Authorization before any `docker create`:** request kind and adapter identity are `synthetic_fixture`; fixture
   bytes, received response bytes, validated patch and policy-computed new content match one allowlist entry by
   SHA-256; base commit and file match the pins. Otherwise `blocked_boundary_unverified` /
   `execution_not_authorized:<detail>` and zero containers.
2. **Trusted files:** every staged file is hash-checked against `demo/pinned.json`; the runner image must carry the
   pinned inputs label (`ira.inputs_sha256`).
3. **Configuration of every created container** (probe, baseline and candidate), via `docker inspect` before start:
   exact image, command, environment list and user; `NetworkMode=none`; no ports, extra hosts or DNS; read-only root;
   not privileged; `CapDrop=[ALL]`, no `CapAdd`; `no-new-privileges`; no devices; exactly two read-only binds; exact
   tmpfs; memory, swap, pids, CPU and ulimits; logging `none`; `--init`; private cgroup and IPC namespaces; no host
   PID/UTS/user namespaces. Any failure: not started, removed, `blocked_boundary_unverified` / `<role>_config:<checks>`.
4. **Probe (trusted script, before candidate code exists in any container):** uid/gid 65534; environment names within
   the image/run allowlist and the docker client's random canary absent; only `lo`; DNS and TCP to `1.1.1.1:443`,
   `169.254.169.254:80` and `172.17.0.1:80` fail; no Docker socket; `CapEff=0`, `NoNewPrivs=1`, `Seccomp=2`; root,
   tree and trusted mounts read-only, `/tmp` writable tmpfs; mount points within an exact allowlist; cgroup memory and
   pids limits and rlimits read back equal to the configured values; no credential paths. Environment names are stored
   in the packet only when an environment check fails.
5. **While running:** stdout (640 KiB) and stderr (64 KiB) are capped while reading; the container is killed when a cap
   is exceeded or the host deadline expires.
6. **Cleanup:** `docker rm -f`, then `docker ps -a` filtered by the run and nonce labels must be empty. A failed cleanup
   is `execution_error`.

## Local observations (trusted synthetic checks, 2026-11-15)

Host: Firecracker VM, kernel `6.18.44-fc-v470`, Docker `29.9.2` (rootful, started manually for this session), runtime
`runc`, cgroup v1. All checks above passed for the probe, baseline and candidate containers. Two defects were caught by
the checks themselves during development and fixed by tightening the flags, not by relaxing the checks: Docker's
default `CgroupnsMode=host` on cgroup v1 (now `--cgroupns private`, required), and the `/usr/sbin/docker-init`
read-only bind added by `--init` (now an exact, read-only-required allowlist entry).

The local VM's own environment holds credentials. They are not passed to containers (the docker client environment is
built from scratch and the probe checks the result), but a container escape would reach them. The local VM is
therefore used for development only (Codex decision).

## Hosted runner observations

Observed in GitHub Actions run `43673596716`, job `boundary-synthetic` (id `132688888619`), commit
`03e2fffe2f8931949d02c422f374460efe741a98`, runner image `ubuntu-24.04`:

| Observation | Value |
|---|---|
| Kernel | `7.0.0-1014-aws` |
| Docker client / server | `28.0.4` / `28.0.4`, runtime `runc` |
| cgroup | v2, driver `systemd`; security options AppArmor, seccomp (builtin profile), cgroupns |
| Runner image built from pinned inputs | `ira-runner:5f083c95e28e`, id `sha256:13415948…` (differs from local builds: image ids are not reproducible) |
| Test suite with `IRA_REQUIRE_DOCKER=1` | 431 passed |
| `ira eval --execute` | matched 82/82 |
| Leftover containers labeled `ira.run` after the job | none |
| `GITHUB_TOKEN`, `ACTIONS_RUNTIME_TOKEN`, `ACTIONS_ID_TOKEN_REQUEST_TOKEN`/`_URL`, `ACTIONS_CACHE_URL`, `ACTIONS_RESULTS_URL` in `run` step environment | all absent |
| `git` `extraheader` after checkout | not configured. The checkout log shows `actions/checkout` wrote the token as an `extraheader` during checkout and removed it before the step ended (`persist-credentials: false`) |
| Runner user in `docker` group | yes |

Interpretation, limited to what was observed: the job token was not present in `run` step environments or in
`.git/config` after checkout, and containers received neither (environment built from scratch, binds limited to two
read-only directories, both checked). The token still exists in the runner's own processes for the job's duration,
and the runner user controls the Docker socket, so a container escape on this host would reach both. Passing probes
on this runner are evidence about this run's containers, not proof that model-generated code would be contained.

## Residual risks

| Risk | Why it remains | Consequence for the project |
|---|---|---|
| Shared kernel; runc and dockerd escapes | Containers are namespaces and cgroups on the host kernel | Probes cannot rule out escapes; not acceptable as isolation for untrusted model output without further review |
| Rootful daemon, no user-namespace remapping | Default Docker configuration on both hosts | uid 65534 in the container is uid 65534 on the host |
| Job token on the hosted runner | `actions/checkout` needs `contents: read` for this private repository; the token exists for the job's duration | Not mounted or passed into containers; with `persist-credentials: false` it is not written to `.git/config`. A container escape on the runner could reach runner processes that hold it |
| Runner user can use the Docker socket | GitHub-hosted runners put the runner user in the `docker` group | The socket is root-equivalent on the runner VM; it is not mounted into containers (checked) |
| In-process tampering | Candidate code is imported by the pytest process and shares a uid with the wrapper | JUnit, exit codes and output are candidate-influenced evidence for human review only |
| Resource limits | Observed and exercised (timeout, OOM, output cap, fork burst) with synthetic fixtures | Not proven complete against hostile code |
| Image build is not byte-reproducible | Two local builds from identical pinned inputs produced different image ids | Inputs are pinned by hash and labeled; each packet records the image id actually used |
| Supply chain | Base image pinned by digest; wheels pinned by hash (trust on first use) | A compromised upstream at pin time would be inherited |
