"""Container-configuration checks and byte-bound authorization (no Docker)."""
import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from conftest import PINNED, ROOT
from ira import runner
from ira.adapters import FixtureAdapter
from ira.authorize import NotAuthorized, authorize
from ira.execution import (FSIZE_BYTES, NOFILE, TEST_COMMAND, TMPFS, Limits, check_config, create_argv,
                           expected_env)
from ira.status import Status

LIM = Limits(memory_mb=256, pids=64, cpus=1, timeout_s=20)
TREE, TRUSTED = Path("/x/tree"), Path("/x/trusted")
IMAGE_ENV = ["PATH=/usr/bin", "LANG=C.UTF-8"]


def good_inspect():
    return {"Image": "sha256:img",
            "Config": {"Cmd": list(TEST_COMMAND), "Entrypoint": None, "Env": expected_env(IMAGE_ENV, "candidate"),
                       "User": "65534:65534", "ExposedPorts": None},
            "Mounts": [{"Type": "bind", "RW": False}, {"Type": "bind", "RW": False}],
            "HostConfig": {"NetworkMode": "none", "PortBindings": {}, "ExtraHosts": None, "Dns": [],
                           "ReadonlyRootfs": True, "Privileged": False, "CapDrop": ["ALL"], "CapAdd": None,
                           "SecurityOpt": ["no-new-privileges"], "Devices": [],
                           "Binds": [f"{TREE}:/work/repo:ro", f"{TRUSTED}:/trusted:ro"], "VolumesFrom": None,
                           "Tmpfs": {"/tmp": TMPFS}, "Memory": 256 * 1024 * 1024, "MemorySwap": 256 * 1024 * 1024,
                           "PidsLimit": 64, "NanoCpus": 1_000_000_000,
                           "Ulimits": [{"Name": "fsize", "Soft": FSIZE_BYTES, "Hard": FSIZE_BYTES},
                                       {"Name": "nofile", "Soft": NOFILE, "Hard": NOFILE}],
                           "LogConfig": {"Type": "none"}, "Init": True, "PidMode": "", "IpcMode": "private",
                           "UTSMode": "", "UsernsMode": "", "CgroupnsMode": "private"}}


def fails(info):
    return check_config(info, image_id="sha256:img", image_env=IMAGE_ENV, role="candidate", limits=LIM, tree=TREE,
                        trusted=TRUSTED, command=TEST_COMMAND)


def test_good_config_passes():
    assert fails(good_inspect()) == []


@pytest.mark.parametrize("path,value,check", [
    ("HostConfig.NetworkMode", "bridge", "network_none"),
    ("HostConfig.NetworkMode", "host", "network_none"),
    ("HostConfig.Privileged", True, "not_privileged"),
    ("HostConfig.CapAdd", ["SYS_ADMIN"], "no_cap_add"),
    ("HostConfig.CapDrop", [], "cap_drop_all"),
    ("HostConfig.SecurityOpt", [], "no_new_privileges"),
    ("HostConfig.ReadonlyRootfs", False, "readonly_rootfs"),
    ("HostConfig.Binds", [f"{TREE}:/work/repo:rw", f"{TRUSTED}:/trusted:ro"], "binds_exact"),
    ("HostConfig.Binds", [f"{TREE}:/work/repo:ro", f"{TRUSTED}:/trusted:ro", "/var/run/docker.sock:/s"], "binds_exact"),
    ("Mounts", [{"Type": "bind", "RW": True}, {"Type": "bind", "RW": False}], "mounts_readonly_binds_only"),
    ("HostConfig.Devices", [{"PathOnHost": "/dev/kvm"}], "no_devices"),
    ("HostConfig.Memory", 0, "memory"),
    ("HostConfig.PidsLimit", None, "pids_limit"),
    ("HostConfig.LogConfig", {"Type": "json-file"}, "log_driver_none"),
    ("HostConfig.PidMode", "host", "namespaces_not_host"),
    ("HostConfig.CgroupnsMode", "host", "cgroupns_private"),
    ("HostConfig.Tmpfs", {"/tmp": "rw,exec"}, "tmpfs_exact"),
    ("HostConfig.PortBindings", {"80/tcp": [{}]}, "no_ports"),
    ("Config.Env", ["AWS_SECRET_ACCESS_KEY=x", *expected_env(IMAGE_ENV, "candidate")], "env_exact"),
    ("Config.User", "0:0", "user"),
    ("Config.Cmd", ["sh", "-c", "anything"], "command"),
    ("Image", "sha256:other", "image"),
])
def test_config_weakening_detected(path, value, check):
    info = good_inspect()
    node = info
    keys = path.split(".")
    for k in keys[:-1]:
        node = node[k]
    node[keys[-1]] = value
    assert check in fails(info)


def test_create_argv_has_no_candidate_controlled_values():
    argv = create_argv("ira-r-candidate-n", "r", "candidate", "n", "sha256:img", LIM, TREE, TRUSTED, TEST_COMMAND)
    for flag in ("--network", "--cgroupns", "--read-only", "--cap-drop", "--log-driver", "--init"):
        assert flag in argv
    assert argv[argv.index("--network") + 1] == "none" and argv[-len(TEST_COMMAND):] == list(TEST_COMMAND)


# --------------------------------------------------------------------------- authorization (amendment 1)
def _identity(case="good_trim_collapse", kind="synthetic_fixture", fixtures_dir=None):
    a = FixtureAdapter(case, **({"fixtures_dir": fixtures_dir} if fixtures_dir else {}))
    return {**a.identity(), "kind": kind}, a


def _entry(case):
    return [e for e in json.loads((ROOT / "runner/synthetic_allowlist.json").read_text())["entries"]
            if e["case_id"] == case][0]


def _auth(identity, **over):
    e = _entry(identity["fixture_case"]) if identity.get("fixture_case") in {x["case_id"] for x in json.loads(
        (ROOT / "runner/synthetic_allowlist.json").read_text())["entries"]} else _entry("good_trim_collapse")
    kw = dict(request_kind="synthetic_fixture", adapter_identity=identity, response_sha256=e["response_sha256"],
              patch_sha256=e["patch_sha256"], new_content_sha256=e["new_content_sha256"],
              base_commit=PINNED["base_commit"], allowlisted_file=PINNED["allowlisted_file"], pinned=PINNED)
    kw.update(over)
    return authorize(**kw)


def test_reviewed_bytes_authorized():
    assert _auth(_identity()[0])["case_id"] == "good_trim_collapse"


@pytest.mark.parametrize("kind", ["live_model", "replay_recording"])
def test_live_and_replay_refused_even_with_known_bytes(kind):
    with pytest.raises(NotAuthorized) as info:
        _auth(_identity(kind=kind)[0])
    assert info.value.detail == "untrusted_execution_not_enabled"
    with pytest.raises(NotAuthorized):
        _auth(_identity()[0], request_kind=kind)


@pytest.mark.parametrize("field", ["response_sha256", "patch_sha256", "new_content_sha256"])
def test_any_digest_mismatch_refused(field):
    with pytest.raises(NotAuthorized) as info:
        _auth(_identity()[0], **{field: "0" * 64})
    assert info.value.detail == f"{field}_mismatch"


def test_modified_fixture_under_allowed_name_refused(tmp_path):
    fx = tmp_path / "fixtures"
    shutil.copytree(ROOT / "fixtures", fx)
    target = fx / "responses" / "good_trim_collapse.json"
    target.write_bytes(target.read_bytes().replace(b'\\" \\".join', b'\\"-\\".join'))
    ident, _ = _identity(fixtures_dir=fx)
    with pytest.raises(NotAuthorized) as info:
        _auth(ident)
    assert info.value.detail == "fixture_sha256_mismatch"


def test_unlisted_case_and_tampered_allowlist_refused():
    with pytest.raises(NotAuthorized) as info:
        _auth(_identity("m2_unreviewed_valid_patch")[0])
    assert info.value.detail == "case_not_allowlisted"
    bad = copy.deepcopy(PINNED)
    bad["synthetic_allowlist"]["sha256"] = "0" * 64
    with pytest.raises(NotAuthorized) as info:
        _auth(_identity()[0], pinned=bad)
    assert info.value.detail == "allowlist_hash_mismatch"


class RecordingExecutor:
    def __init__(self):
        self.calls = 0

    def run(self, **kw):
        self.calls += 1
        raise AssertionError("executor must not be reached")


def test_tampered_fixture_never_reaches_executor(built_repo, write_request, tmp_path):
    fx = tmp_path / "fixtures"
    shutil.copytree(ROOT / "fixtures", fx)
    target = fx / "responses" / "good_trim_collapse.json"
    target.write_bytes(target.read_bytes().replace(b'\\" \\".join', b'\\"-\\".join'))
    ex = RecordingExecutor()
    pkt, _ = runner.execute(write_request("good_trim_collapse", built_repo), tmp_path / "o", work_root=tmp_path / "w",
                            adapter_factory=lambda s: FixtureAdapter(s.fixture_case, fixtures_dir=fx), executor=ex)
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("blocked_boundary_unverified",
                                                              "execution_not_authorized:fixture_sha256_mismatch")
    assert ex.calls == 0 and pkt["execution"]["candidate_executed"] is False


def test_live_identity_never_reaches_executor(built_repo, write_request, tmp_path):
    class LiveLike(FixtureAdapter):
        def identity(self):
            return {**super().identity(), "kind": "live_model", "model": "not-a-real-call"}
    ex = RecordingExecutor()
    pkt, _ = runner.execute(write_request("good_trim_collapse", built_repo), tmp_path / "o", work_root=tmp_path / "w",
                            adapter_factory=lambda s: LiveLike(s.fixture_case), executor=ex)
    assert pkt["status_reason"] == "execution_not_authorized:untrusted_execution_not_enabled" and ex.calls == 0


def test_pins_current():
    import subprocess
    import sys
    assert subprocess.run([sys.executable, str(ROOT / "tools/pin_runner.py"), "--check"]).returncode == 0
    for rel, digest in PINNED["runner"]["files"].items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest
