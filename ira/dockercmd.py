"""Second and last process entry point: the docker CLI with a fixed argv shape and a scrubbed environment.

* Nothing is inherited from the caller's environment except an explicit DOCKER_HOST override (used by tests).
* DOCKER_CONFIG points to an empty private directory, so the client never reads ~/.docker credentials.
* A random canary variable is set in the client's own environment; probes check it never appears in a container.
* Subcommands are limited to a fixed list. Callers build argv from trusted values only.
"""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

ALLOWED = {
    "version": None, "info": None, "build": None, "create": None, "inspect": None, "start": None, "kill": None,
    "rm": None, "ps": None, "image": {"inspect"},
}
# Subcommands used once a candidate tree exists (probe and test containers).
CANDIDATE_PATH = frozenset({"create", "inspect", "start", "kill", "rm", "ps"})
CLIENT_CANARY_NAME = "IRA_CLIENT_CANARY"
CLIENT_CANARY_VALUE = secrets.token_hex(16)
MAX_OUTPUT = 4 * 1024 * 1024
_SAFE_PATH = "/usr/bin:/bin:/usr/local/bin"
_CONFIG_DIR = tempfile.mkdtemp(prefix="ira-docker-config-")


class DockerError(RuntimeError):
    pass


@dataclass(frozen=True)
class DockerResult:
    args: tuple
    returncode: int
    stdout: bytes
    stderr: bytes


def docker_executable() -> str:
    found = shutil.which("docker", path=_SAFE_PATH)
    if not found:
        raise DockerError("docker not found on the trusted PATH")
    return found


def client_env(docker_host: str | None = None) -> dict:
    env = {"PATH": _SAFE_PATH, "HOME": _CONFIG_DIR, "DOCKER_CONFIG": _CONFIG_DIR, "LC_ALL": "C", "LANG": "C",
           "DOCKER_CLI_HINTS": "false", CLIENT_CANARY_NAME: CLIENT_CANARY_VALUE}
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    return env


def _argv(args) -> list:
    args = list(args)
    if not args or args[0] not in ALLOWED:
        raise DockerError(f"docker subcommand not allowed: {args[:1]!r}")
    sub = ALLOWED[args[0]]
    if sub is not None and (len(args) < 2 or args[1] not in sub):
        raise DockerError(f"docker {args[0]} subcommand not allowed")
    for a in args:
        if type(a) is not str or "\0" in a or "\n" in a or len(a) > 8192:
            raise DockerError("docker argument rejected")
    return [docker_executable(), *args]


def run_docker(args, *, timeout: float = 60, check: bool = True, docker_host: str | None = None) -> DockerResult:
    argv = _argv(args)
    try:
        proc = subprocess.run(argv, capture_output=True, env=client_env(docker_host), timeout=timeout, check=False,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DockerError(f"docker {args[0]} could not complete: {type(error).__name__}") from None
    if len(proc.stdout) > MAX_OUTPUT or len(proc.stderr) > MAX_OUTPUT:
        raise DockerError(f"docker {args[0]} output too large")
    result = DockerResult(tuple(args), proc.returncode, proc.stdout, proc.stderr)
    if check and proc.returncode != 0:
        raise DockerError(f"docker {args[0]} exited {proc.returncode}: {proc.stderr[-300:].decode('utf-8', 'replace')}")
    return result


def popen_docker(args, *, docker_host: str | None = None) -> subprocess.Popen:
    """For `docker start -a` only: the caller reads both pipes with byte caps and enforces the deadline."""
    if list(args[:2]) != ["start", "-a"]:
        raise DockerError("popen is only for 'docker start -a'")
    return subprocess.Popen(_argv(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            env=client_env(docker_host))
