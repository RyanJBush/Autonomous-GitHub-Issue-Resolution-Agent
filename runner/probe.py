"""Trusted boundary probe (pinned by hash; mounted read-only at /trusted). Runs in a container created with exactly the
same flags and mounts as the test containers, before any candidate code runs. Reports observations only; the host
decides whether they meet policy. Never imports anything from /work/repo.
"""
import json
import os
import resource
import socket
import sys


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _status_fields():
    out = {}
    for line in (_read("/proc/self/status") or "").splitlines():
        key, _, value = line.partition(":")
        if key in ("CapEff", "CapPrm", "CapBnd", "NoNewPrivs", "Seccomp", "Uid", "Gid"):
            out[key] = value.strip()
    return out


def _mounts():
    rows = []
    for line in (_read("/proc/self/mountinfo") or "").splitlines():
        left, _, right = line.partition(" - ")
        fields, rfields = left.split(), right.split()
        if len(fields) >= 6 and rfields:
            rows.append({"mount_point": fields[4], "options": fields[5], "fstype": rfields[0]})
    return rows


def _tcp(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return "connected"
    except OSError as error:
        return f"blocked:{type(error).__name__}"


def _dns(name):
    try:
        socket.getaddrinfo(name, 443)
        return "resolved"
    except OSError as error:
        return f"failed:{type(error).__name__}"


def _can_write(directory):
    path = os.path.join(directory, ".ira-probe-write")
    try:
        with open(path, "w") as handle:
            handle.write("x")
        os.unlink(path)
        return True
    except OSError:
        return False


def _cgroup():
    v2_mem, v2_pids = _read("/sys/fs/cgroup/memory.max"), _read("/sys/fs/cgroup/pids.max")
    if v2_mem is not None:
        return {"version": 2, "memory_limit": v2_mem, "pids_max": v2_pids,
                "swap_max": _read("/sys/fs/cgroup/memory.swap.max")}
    return {"version": 1, "memory_limit": _read("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
            "pids_max": _read("/sys/fs/cgroup/pids/pids.max"),
            "memsw_limit": _read("/sys/fs/cgroup/memory/memory.memsw.limit_in_bytes")}


def main():
    home = os.environ.get("HOME", "/")
    credential_paths = ["/root/.aws", "/root/.docker", "/root/.config/gh", "/root/.ssh", "/root/.git-credentials",
                        os.path.join(home, ".aws"), os.path.join(home, ".docker"), os.path.join(home, ".ssh"),
                        "/work/repo/.git", "/work/repo/.env", "/run/secrets"]
    report = {
        "probe": "ira.probe.v1",
        "uid": os.getuid(), "gid": os.getgid(),
        "env_names": sorted(os.environ),
        "interfaces": sorted(line.split(":")[0].strip() for line in (_read("/proc/net/dev") or "").splitlines()[2:]),
        "dns_pypi": _dns("pypi.org"),
        "tcp": {"1.1.1.1:443": _tcp("1.1.1.1", 443), "169.254.169.254:80": _tcp("169.254.169.254", 80),
                "172.17.0.1:80": _tcp("172.17.0.1", 80)},
        "mounts": _mounts(),
        "docker_socket_present": any(os.path.exists(p) for p in ("/var/run/docker.sock", "/run/docker.sock")),
        "status": _status_fields(),
        "writable": {d: _can_write(d) for d in ("/", "/work/repo", "/trusted", "/tmp")},
        "cgroup": _cgroup(),
        "rlimit_fsize": resource.getrlimit(resource.RLIMIT_FSIZE)[0],
        "rlimit_nofile": resource.getrlimit(resource.RLIMIT_NOFILE)[0],
        "credential_paths_present": [p for p in credential_paths if os.path.lexists(p)],
        "python": sys.version.split()[0],
    }
    sys.stdout.write(json.dumps(report, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
