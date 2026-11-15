"""Cleanup failure takes precedence over configuration failure at every stage (Codex M2 review fix).

Part 1 is pure fault injection (fake docker preflight and fake containers; no Docker needed).
Part 2 uses real Docker with a weakened argv and a simulated cleanup failure after the real cleanup ran.
"""
import json
import os

import pytest

from conftest import PINNED
from ira import dockercmd, execution, packet, runner, synthetic_exec
from ira.evaluate import make_request
from ira.execution import ContainerResult
from ira.synthetic_exec import DockerExecutor, runner_image_tag
from ira.verdict import Outcome
from ira.status import Status

ROLES = ["probe", "baseline", "candidate"]
LATER = {"probe": [], "baseline": ["probe"], "candidate": ["probe", "baseline"]}


def _request(repo, tmp_path, run_id):
    path = tmp_path / f"{run_id}.json"
    path.write_text(json.dumps(make_request("good_trim_collapse", repo, run_id)))
    return path


@pytest.fixture
def fake_docker(monkeypatch):
    """Fake preflight answers and fake containers; `plan` says which stage fails and how."""
    plan = {}

    def fake_run_docker(args, **kw):
        if args[0] == "version":
            return dockercmd.DockerResult(tuple(args), 0, b"0.0.0-fake\n", b"")
        if args[0] == "info":
            return dockercmd.DockerResult(tuple(args), 0, b'{"CgroupVersion": "2"}', b"")
        if args[:2] == ["image", "inspect"]:
            img = [{"Id": "sha256:fake", "Config": {"Env": [], "Labels": {
                "ira.inputs_sha256": PINNED["runner"]["image_inputs_sha256"]}}}]
            return dockercmd.DockerResult(tuple(args), 0, json.dumps(img).encode(), b"")
        raise AssertionError(f"unexpected docker call in fake mode: {args[:2]}")

    def fake_run_container(*, role, **kw):
        target = role == plan["role"]
        res = ContainerResult(role=role, name=f"fake-{role}", argv=["create", "fake"], container_id="fake",
                              cleanup_ok=True, cleanup_detail="removed; label filter empty")
        if target and plan.get("config_fail"):
            res.config_failures = ["network_none"]
        else:
            res.started, res.exit_code, res.oom_killed = True, 0, False
            res.stdout = b'{"probe": "ira.probe.v1"}\n'
            res.timed_out = bool(target and plan.get("timeout"))
        if target and plan.get("cleanup_fail"):
            res.cleanup_ok, res.cleanup_detail = False, "injected: container still listed after rm -f"
        return res

    monkeypatch.setattr(synthetic_exec.dockercmd, "run_docker", fake_run_docker)
    monkeypatch.setattr(execution, "run_container", fake_run_container)
    monkeypatch.setattr(synthetic_exec, "evaluate_probe", lambda report, **kw: [])
    monkeypatch.setattr(synthetic_exec, "decide_baseline",
                        lambda res, ids, fails: Outcome(True, None, "baseline_failed_as_expected", 1, {}))
    monkeypatch.setattr(synthetic_exec, "decide_candidate",
                        lambda res, ids: Outcome(False, Status.TESTS_PASSED_AWAITING_HUMAN_REVIEW,
                                                 "all_expected_tests_passed", 0, {}))
    return plan


def _execute(fake_docker, role, built_repo, tmp_path, **plan):
    fake_docker.clear()
    fake_docker.update(role=role, **plan)
    run_id = f"{role}-{'-'.join(sorted(plan))}"
    pkt, d = runner.execute(_request(built_repo, tmp_path, run_id), tmp_path / "out", work_root=tmp_path / "w",
                            executor=DockerExecutor(PINNED))
    packet.verify(d)
    return pkt


@pytest.mark.parametrize("role", ROLES)
def test_cleanup_failure_beats_config_failure(fake_docker, role, built_repo, tmp_path):
    pkt = _execute(fake_docker, role, built_repo, tmp_path, config_fail=True, cleanup_fail=True)
    assert (pkt["terminal_status"], pkt["status_reason"]) == (
        "execution_error", f"{role}_cleanup_failed_after_config_failure:network_none")
    recs = pkt["execution"]["containers"]
    assert [c["role"] for c in recs] == LATER[role] + [role]  # no later stage was created
    last = recs[-1]
    assert last["config_failures"] == ["network_none"] and last["cleanup_ok"] is False
    assert last["cleanup_detail"].startswith("injected")
    events = [(t["stage"], t["event"], t["code"]) for t in pkt["trace"]]
    assert (role, "config_failed", "network_none") in events
    assert any(e[:2] == (role, "cleanup_failed") and e[2].startswith("injected") for e in events)
    assert pkt["execution"]["candidate_executed"] is False


@pytest.mark.parametrize("role", ROLES)
def test_config_failure_with_successful_cleanup_still_blocks(fake_docker, role, built_repo, tmp_path):
    pkt = _execute(fake_docker, role, built_repo, tmp_path, config_fail=True)
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("blocked_boundary_unverified",
                                                              f"{role}_config:network_none")
    assert pkt["execution"]["containers"][-1]["cleanup_ok"] is True


@pytest.mark.parametrize("role", ROLES)
def test_cleanup_failure_alone_is_execution_error(fake_docker, role, built_repo, tmp_path):
    pkt = _execute(fake_docker, role, built_repo, tmp_path, cleanup_fail=True)
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("execution_error", f"{role}_cleanup_failed")


@pytest.mark.parametrize("role", ["baseline", "candidate"])
def test_cleanup_failure_after_timeout_keeps_both_facts(fake_docker, role, built_repo, tmp_path):
    pkt = _execute(fake_docker, role, built_repo, tmp_path, timeout=True, cleanup_fail=True)
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("execution_error", f"{role}_cleanup_failed_after_timeout")
    assert pkt["execution"]["containers"][-1]["timed_out"] is True


def test_packet_states_image_trust_limitation(fake_docker, built_repo, tmp_path):
    pkt = _execute(fake_docker, "candidate", built_repo, tmp_path, config_fail=True)
    assert "do not independently prove" in pkt["execution"]["image"]["trust_note"]
    assert any("traceability, not independent proof" in r for r in pkt["execution"]["residual_risks"])


# --------------------------------------------------------------------------- part 2: real Docker
@pytest.fixture(scope="module")
def docker_ready():
    try:
        dockercmd.run_docker(["version", "--format", "{{.Server.Version}}"], timeout=15)
        dockercmd.run_docker(["image", "inspect", runner_image_tag(PINNED)], timeout=15)
    except dockercmd.DockerError as error:
        if os.environ.get("IRA_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker required but unavailable: {error}")
        pytest.skip(f"Docker or runner image unavailable: {error}")


@pytest.mark.parametrize("role", ROLES)
def test_real_docker_combined_failure(docker_ready, role, built_repo, tmp_path, monkeypatch):
    real_cleanup = execution._cleanup

    def cleanup_then_report_failure(res, run_id, nonce, docker_host):
        real_cleanup(res, run_id, nonce, docker_host)  # the container really is removed
        if res.role == role:
            res.cleanup_ok, res.cleanup_detail = False, f"injected after real cleanup ({res.cleanup_detail})"
    monkeypatch.setattr(execution, "_cleanup", cleanup_then_report_failure)

    def drop_network_for_role(argv):
        if f"ira.role={role}" in argv:
            i = argv.index("--network")
            return argv[:i] + argv[i + 2:]
        return argv
    run_id = f"real-{role}"
    pkt, d = runner.execute(_request(built_repo, tmp_path, run_id), tmp_path / "out", work_root=tmp_path / "w",
                            executor=DockerExecutor(PINNED, argv_hook=drop_network_for_role))
    packet.verify(d)
    assert pkt["terminal_status"] == "execution_error"
    assert pkt["status_reason"].startswith(f"{role}_cleanup_failed_after_config_failure:")
    assert "network_none" in pkt["status_reason"]
    last = pkt["execution"]["containers"][-1]
    assert last["role"] == role and last["started"] is False and "network_none" in last["config_failures"]
    assert dockercmd.run_docker(["ps", "-a", "-q", "--filter", f"label=ira.run={run_id}"]).stdout.strip() == b""
