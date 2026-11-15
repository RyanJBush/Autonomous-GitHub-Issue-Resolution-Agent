"""Container execution for trusted synthetic checks (Milestone 2).

Every container (probe, baseline, candidate) is created with one fixed argv shape, inspected and checked against the
expected configuration before it is started, read with byte caps while it runs, killed at the host deadline or when a
cap is exceeded, and removed; removal is then verified. This module never decides a terminal status.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import dockercmd
from .demo_repo import PROJECT_ROOT
from .gitcmd import run_git

TRUSTED_MOUNT, REPO_MOUNT = "/trusted", "/work/repo"
TMPFS = "rw,nosuid,nodev,noexec,size=64m"
FSIZE_BYTES = 33_554_432
NOFILE = 256
TEST_COMMAND = ("python", "-I", "-B", "/trusted/run_tests.py")
PROBE_COMMAND = ("python", "-I", "-B", "/trusted/probe.py")
EXTRA_ENV = ("PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",)
STDOUT_CAP, STDERR_CAP = 655_360, 65_536
PROBE_TIMEOUT_S = 30
KILL_GRACE_S = 10
STAGED_FILES = {  # staged name -> pinned source path
    "test_issue.py": "demo/trusted_tests/test_issue.py",
    "pytest.ini": "runner/pytest.ini",
    "run_tests.py": "runner/run_tests.py",
    "probe.py": "runner/probe.py",
}


class ExecutionSetupError(Exception):
    def __init__(self, code, detail):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Limits:
    memory_mb: int
    pids: int
    cpus: int
    timeout_s: int


@dataclass
class ContainerResult:
    role: str
    name: str
    argv: list
    container_id: str | None = None
    config_failures: list = field(default_factory=list)
    started: bool = False
    timed_out: bool = False
    output_limit_exceeded: str | None = None
    exit_code: int | None = None
    oom_killed: bool | None = None
    duration_s: float | None = None
    stdout: bytes = b""
    stderr: bytes = b""
    cleanup_ok: bool = False
    cleanup_detail: str = ""
    error: str | None = None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- staging
def _make_world_readable(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, 0o555)
        for name in filenames:
            os.chmod(os.path.join(dirpath, name), 0o444)


def new_work_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="ira-exec-"))
    os.chmod(root, 0o755)  # uid 65534 must traverse it to reach the read-only mounts
    return root


def stage_trusted(work: Path, pinned: dict) -> Path:
    """Copy pinned trusted files after checking each hash. Raises ExecutionSetupError on any mismatch."""
    expected = dict(pinned["runner"]["files"])
    expected[pinned["trusted_test"]["path"]] = pinned["trusted_test"]["sha256"]
    out = work / "trusted"
    out.mkdir()
    for staged, src in STAGED_FILES.items():
        path = PROJECT_ROOT / src
        if path.is_symlink() or not path.is_file():
            raise ExecutionSetupError("trusted_file_missing", src)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected.get(src):
            raise ExecutionSetupError("trusted_file_mismatch", src)
        (out / staged).write_bytes(data)
    _make_world_readable(out)
    return out


def materialize_tree(work: Path, name: str, repo_path: str, base_commit: str, replace: dict | None = None) -> Path:
    """Write the pinned base tree from git objects (never from the work tree); optionally replace file contents."""
    out = work / name
    out.mkdir()
    env = {"GIT_DIR": str(Path(repo_path) / ".git"), "GIT_WORK_TREE": str(repo_path)}
    listing = run_git(["ls-tree", "-r", "-z", base_commit], cwd=repo_path, env_extra=env).stdout
    replace = dict(replace or {})
    for entry in filter(None, listing.split(b"\0")):
        meta, _, rel = entry.decode("utf-8").partition("\t")
        mode, kind, blob = meta.split(" ")
        if kind != "blob" or mode != "100644" or rel.startswith("/") or ".." in rel.split("/"):
            raise ExecutionSetupError("tree_entry_not_allowed", "base tree must contain only regular files")
        content = replace.pop(rel) if rel in replace else run_git(["cat-file", "blob", blob], cwd=repo_path,
                                                                    env_extra=env).stdout
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    if replace:
        raise ExecutionSetupError("replacement_path_not_in_tree", "replacement targets a path absent from the base")
    _make_world_readable(out)
    return out


def remove_work(work: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(work):
        os.chmod(dirpath, 0o755)
    shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- container configuration
def create_argv(name: str, run_id: str, role: str, nonce: str, image_id: str, limits: Limits, tree: Path,
                trusted: Path, command: tuple) -> list:
    return [
        "create", "--name", name, "--label", f"ira.run={run_id}", "--label", f"ira.role={role}",
        "--label", f"ira.nonce={nonce}",
        "--network", "none", "--cgroupns", "private", "--ipc", "private", "--read-only", "--tmpfs", f"/tmp:{TMPFS}",
        "--memory", f"{limits.memory_mb}m", "--memory-swap", f"{limits.memory_mb}m",
        "--pids-limit", str(limits.pids), "--cpus", str(limits.cpus),
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
        "--ulimit", f"fsize={FSIZE_BYTES}", "--ulimit", f"nofile={NOFILE}", "--log-driver", "none", "--init",
        *[x for e in EXTRA_ENV for x in ("--env", e)], "--env", f"IRA_ROLE={role}",
        "-v", f"{tree}:{REPO_MOUNT}:ro", "-v", f"{trusted}:{TRUSTED_MOUNT}:ro", "-w", TRUSTED_MOUNT,
        image_id, *command,
    ]


def expected_env(image_env: list, role: str) -> list:
    return sorted([*image_env, *EXTRA_ENV, f"IRA_ROLE={role}"])


def check_config(info: dict, *, image_id: str, image_env: list, role: str, limits: Limits, tree: Path,
                 trusted: Path, command: tuple) -> list[str]:
    """Return the names of failed checks for a created (not yet started) container's `docker inspect` output."""
    h, c = info.get("HostConfig") or {}, info.get("Config") or {}
    want_binds = sorted([f"{tree}:{REPO_MOUNT}:ro", f"{trusted}:{TRUSTED_MOUNT}:ro"])
    mounts = info.get("Mounts") or []
    ulimits = {u.get("Name"): (u.get("Soft"), u.get("Hard")) for u in (h.get("Ulimits") or [])}
    checks = {
        "image": info.get("Image") == image_id,
        "command": list(c.get("Cmd") or []) == list(command) and not c.get("Entrypoint"),
        "env_exact": sorted(c.get("Env") or []) == expected_env(image_env, role),
        "user": c.get("User") == "65534:65534",
        "network_none": h.get("NetworkMode") == "none",
        "no_ports": not h.get("PortBindings") and not c.get("ExposedPorts"),
        "no_extra_hosts_or_dns": not h.get("ExtraHosts") and not h.get("Dns"),
        "readonly_rootfs": h.get("ReadonlyRootfs") is True,
        "not_privileged": h.get("Privileged") is False,
        "cap_drop_all": [x.upper() for x in (h.get("CapDrop") or [])] in (["ALL"], ["CAP_ALL"]),
        "no_cap_add": not h.get("CapAdd"),
        "no_new_privileges": any(s.startswith("no-new-privileges") for s in (h.get("SecurityOpt") or [])),
        "no_devices": not h.get("Devices"),
        "binds_exact": sorted(h.get("Binds") or []) == want_binds,
        "mounts_readonly_binds_only": len(mounts) == 2 and all(m.get("Type") == "bind" and m.get("RW") is False
                                                               for m in mounts),
        "no_volumes_from": not h.get("VolumesFrom"),
        "tmpfs_exact": (h.get("Tmpfs") or {}) == {"/tmp": TMPFS},
        "memory": h.get("Memory") == limits.memory_mb * 1024 * 1024,
        "memory_swap": h.get("MemorySwap") == limits.memory_mb * 1024 * 1024,
        "pids_limit": h.get("PidsLimit") == limits.pids,
        "cpus": h.get("NanoCpus") == limits.cpus * 1_000_000_000,
        "ulimit_fsize": ulimits.get("fsize") == (FSIZE_BYTES, FSIZE_BYTES),
        "ulimit_nofile": ulimits.get("nofile") == (NOFILE, NOFILE),
        "log_driver_none": (h.get("LogConfig") or {}).get("Type") == "none",
        "init": h.get("Init") is True,
        "namespaces_not_host": all(not str(h.get(k) or "").startswith(("host", "container:"))
                                   for k in ("PidMode", "IpcMode", "UTSMode", "UsernsMode", "NetworkMode")),
        "cgroupns_private": h.get("CgroupnsMode") == "private",
        "ipc_private": h.get("IpcMode") == "private",
    }
    return sorted(k for k, ok in checks.items() if not ok)


# --------------------------------------------------------------------------- running
class _Reader(threading.Thread):
    def __init__(self, stream, cap):
        super().__init__(daemon=True)
        self.stream, self.cap, self.buf, self.over = stream, cap, bytearray(), False

    def run(self):
        while True:
            chunk = self.stream.read1(65536) if hasattr(self.stream, "read1") else self.stream.read(65536)
            if not chunk:
                return
            if not self.over:
                room = self.cap - len(self.buf)
                self.buf += chunk[:max(room, 0)]
                if len(chunk) > room:
                    self.over = True


def run_container(*, role: str, run_id: str, image_id: str, image_env: list, limits: Limits, tree: Path,
                  trusted: Path, command: tuple, deadline_s: float, docker_host: str | None = None,
                  argv_hook=None) -> ContainerResult:
    nonce = secrets.token_hex(6)
    name = f"ira-{run_id}-{role}-{nonce}"
    argv = create_argv(name, run_id, role, nonce, image_id, limits, tree, trusted, command)
    if argv_hook:  # tests only: lets a test weaken the argv to prove the inspection catches it
        argv = argv_hook(argv)
    res = ContainerResult(role=role, name=name, argv=argv)
    try:
        try:
            res.container_id = dockercmd.run_docker(argv, docker_host=docker_host).stdout.decode().strip()
        except dockercmd.DockerError as error:
            res.error = f"create_failed:{str(error)[:200]}"
            return res
        info = json.loads(dockercmd.run_docker(["inspect", "--type", "container", name],
                                               docker_host=docker_host).stdout)[0]
        res.config_failures = check_config(info, image_id=image_id, image_env=image_env, role=role, limits=limits,
                                           tree=tree, trusted=trusted, command=command)
        if res.config_failures:
            return res
        proc = dockercmd.popen_docker(["start", "-a", name], docker_host=docker_host)
        res.started = True
        out, err = _Reader(proc.stdout, STDOUT_CAP), _Reader(proc.stderr, STDERR_CAP)
        out.start(), err.start()
        started = time.monotonic()
        while proc.poll() is None:
            if time.monotonic() - started > deadline_s:
                res.timed_out = True
                break
            if out.over or err.over:
                res.output_limit_exceeded = "stdout" if out.over else "stderr"
                break
            time.sleep(0.05)
        if proc.poll() is None:
            dockercmd.run_docker(["kill", name], check=False, docker_host=docker_host)
            try:
                proc.wait(timeout=KILL_GRACE_S)
            except Exception:
                proc.kill()
                proc.wait(timeout=KILL_GRACE_S)
        res.duration_s = round(time.monotonic() - started, 3)
        out.join(timeout=KILL_GRACE_S), err.join(timeout=KILL_GRACE_S)
        if not res.output_limit_exceeded and (out.over or err.over):
            res.output_limit_exceeded = "stdout" if out.over else "stderr"
        res.stdout, res.stderr = bytes(out.buf), bytes(err.buf)
        state = json.loads(dockercmd.run_docker(["inspect", "--type", "container", name],
                                                docker_host=docker_host).stdout)[0].get("State") or {}
        res.exit_code, res.oom_killed = state.get("ExitCode"), bool(state.get("OOMKilled"))
        if state.get("Running"):
            res.error = "container_still_running_after_kill"
    except (dockercmd.DockerError, ValueError, OSError, IndexError) as error:
        res.error = res.error or f"docker_error:{str(error)[:200]}"
    finally:
        _cleanup(res, run_id, nonce, docker_host)
    return res


def _cleanup(res: ContainerResult, run_id: str, nonce: str, docker_host) -> None:
    try:
        dockercmd.run_docker(["rm", "-f", res.name], check=False, docker_host=docker_host)
        left = dockercmd.run_docker(["ps", "-a", "-q", "--filter", f"label=ira.nonce={nonce}",
                                     "--filter", f"label=ira.run={run_id}"], docker_host=docker_host).stdout.strip()
        res.cleanup_ok = left == b""
        res.cleanup_detail = "removed; label filter empty" if res.cleanup_ok else "container still listed after rm -f"
    except dockercmd.DockerError as error:
        res.cleanup_ok, res.cleanup_detail = False, f"cleanup check failed: {str(error)[:160]}"


def container_record(res: ContainerResult, max_excerpt: int = 16_384) -> dict:
    """Bounded, JSON-safe record for the packet (stdout/stderr are candidate-influenced)."""
    def excerpt(b):
        return {"text": b[:max_excerpt].decode("utf-8", "replace"), "bytes": len(b), "truncated": len(b) > max_excerpt}
    return {"role": res.role, "name": res.name, "container_id": res.container_id, "argv": res.argv,
            "config_failures": res.config_failures, "started": res.started, "timed_out": res.timed_out,
            "output_limit_exceeded": res.output_limit_exceeded, "exit_code": res.exit_code,
            "oom_killed": res.oom_killed, "duration_s": res.duration_s, "cleanup_ok": res.cleanup_ok,
            "cleanup_detail": res.cleanup_detail, "error": res.error,
            "stdout": excerpt(res.stdout), "stderr": excerpt(res.stderr)}
