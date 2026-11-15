"""Build the trusted runner image (operator command, never on the candidate path).

1. Hash-check runner/Dockerfile and runner/requirements.lock against demo/pinned.json.
2. Download the locked wheels on the host with `pip download --require-hashes` (network: PyPI only).
3. `docker build --network none` from a private context; label the image with the pinned inputs hash.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import dockercmd
from .demo_repo import PROJECT_ROOT, load_pinned
from .synthetic_exec import runner_image_tag


def inputs_sha256() -> str:
    h = hashlib.sha256()
    for rel in ("runner/Dockerfile", "runner/requirements.lock"):
        h.update(rel.encode() + b"\0" + (PROJECT_ROOT / rel).read_bytes() + b"\0")
    return h.hexdigest()


def build(docker_host: str | None = None) -> dict:
    pinned = load_pinned()
    for rel in ("runner/Dockerfile", "runner/requirements.lock"):
        if hashlib.sha256((PROJECT_ROOT / rel).read_bytes()).hexdigest() != pinned["runner"]["files"][rel]:
            raise RuntimeError(f"{rel} does not match its pinned hash")
    digest = inputs_sha256()
    if digest != pinned["runner"]["image_inputs_sha256"]:
        raise RuntimeError("runner image inputs do not match the pinned hash")
    ctx = Path(tempfile.mkdtemp(prefix="ira-image-"))
    try:
        shutil.copy(PROJECT_ROOT / "runner/Dockerfile", ctx / "Dockerfile")
        shutil.copy(PROJECT_ROOT / "runner/requirements.lock", ctx / "requirements.lock")
        subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check",
                        "--only-binary=:all:", "--no-deps", "--require-hashes", "--python-version", "3.12",
                        "--platform", "any", "-d", str(ctx / "wheels"), "-r", str(ctx / "requirements.lock")],
                       check=True, timeout=300)
        tag = runner_image_tag(pinned)
        dockercmd.run_docker(["build", "--network", "none", "--label", f"ira.inputs_sha256={digest}",
                              "-t", tag, str(ctx)], timeout=600, docker_host=docker_host)
        image_id = dockercmd.run_docker(["image", "inspect", "--format", "{{.Id}}", tag],
                                        docker_host=docker_host).stdout.decode().strip()
        return {"tag": tag, "id": image_id, "inputs_sha256": digest}
    finally:
        shutil.rmtree(ctx, ignore_errors=True)
