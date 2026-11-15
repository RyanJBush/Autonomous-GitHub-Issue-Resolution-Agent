"""Trusted synthetic execution in Docker. Hand-written fixtures only; never model output.

Skipped when Docker or the runner image is unavailable, unless IRA_REQUIRE_DOCKER=1, in which case they fail.
"""
import copy
import json
import os
import subprocess

import pytest

from conftest import CANARY, PINNED
from ira import dockercmd, runner
from ira.synthetic_exec import DockerExecutor, runner_image_tag


@pytest.fixture(scope="module")
def docker_ready():
    try:
        dockercmd.run_docker(["version", "--format", "{{.Server.Version}}"], timeout=15)
        dockercmd.run_docker(["image", "inspect", runner_image_tag(PINNED)], timeout=15)
    except dockercmd.DockerError as error:
        if os.environ.get("IRA_REQUIRE_DOCKER") == "1":
            pytest.fail(f"Docker required but unavailable: {error}")
        pytest.skip(f"Docker or runner image unavailable: {error}")


def run(case, repo, write_request, tmp_path, *, executor=None, pinned=None, run_id=None, **req_kw):
    from ira.evaluate import make_request
    req = make_request(case, repo, run_id or case)
    for key, value in req_kw.items():
        req["execution"][key] = value
    path = tmp_path / f"{run_id or case}.json"
    path.write_text(json.dumps(req))
    ex = executor or DockerExecutor(pinned or PINNED)
    pkt, d = runner.execute(path, tmp_path / "out", work_root=tmp_path / "w", executor=ex, pinned=pinned)
    return pkt, ex


def leftovers(run_id):
    return dockercmd.run_docker(["ps", "-a", "-q", "--filter", f"label=ira.run={run_id}"]).stdout.strip()


def test_known_good_baseline_fails_expected_and_candidate_passes(docker_ready, built_repo, write_request, tmp_path):
    pkt, ex = run("good_trim_collapse", built_repo, write_request, tmp_path)
    tt = PINNED["trusted_test"]
    assert pkt["terminal_status"] == "tests_passed_awaiting_human_review"
    base, cand = pkt["tests"]["baseline"], pkt["tests"]["candidate"]
    assert {t for t, v in base["tests"].items() if v == "failed"} == set(tt["expected_base_failures"])
    assert base["pytest_exit"] == 1 and cand["pytest_exit"] == 0
    assert set(cand["tests"].values()) == {"passed"} and sorted(cand["tests"]) == sorted(tt["test_ids"])
    roles = [c["role"] for c in pkt["execution"]["containers"]]
    assert roles == ["probe", "baseline", "candidate"] and ex.calls_create == 3
    assert all(c["config_failures"] == [] and c["cleanup_ok"] for c in pkt["execution"]["containers"])
    def norm(argv):  # everything except name, labels, role and the tree path must be identical
        out = []
        for a in argv:
            if a.startswith("ira-"):
                out.append("<name>")
            elif a.startswith(("ira.nonce=", "ira.role=", "IRA_ROLE=")):
                out.append(a.split("=")[0])
            elif a.endswith(":/work/repo:ro"):
                out.append("<tree>:/work/repo:ro")
            else:
                out.append(a)
        return out
    _, base_c, cand_c = pkt["execution"]["containers"]
    assert norm(base_c["argv"]) == norm(cand_c["argv"])
    assert base_c["argv"][base_c["argv"].index("-v") + 1] != cand_c["argv"][cand_c["argv"].index("-v") + 1]
    assert pkt["execution"]["probe"]["passed"] and "env_names" not in pkt["execution"]["probe"]["observations"]
    assert pkt["execution"]["verified_for_model_generated_code"] is False
    assert leftovers("good_trim_collapse") == b""


def test_probe_config_weakening_blocks_before_any_test_container(docker_ready, built_repo, write_request, tmp_path):
    def drop_network(argv):
        i = argv.index("--network")
        return argv[:i] + argv[i + 2:]
    ex = DockerExecutor(PINNED, argv_hook=drop_network)
    pkt, _ = run("good_trim_collapse", built_repo, write_request, tmp_path, executor=ex, run_id="weak-probe")
    assert pkt["terminal_status"] == "blocked_boundary_unverified"
    assert pkt["status_reason"].startswith("probe_config:") and "network_none" in pkt["status_reason"]
    assert [c["role"] for c in pkt["execution"]["containers"]] == ["probe"]
    assert pkt["execution"]["containers"][0]["started"] is False and leftovers("weak-probe") == b""


def test_actual_candidate_container_is_inspected_too(docker_ready, built_repo, write_request, tmp_path):
    def weaken_candidate_only(argv):
        if "ira.role=candidate" in argv:
            i = argv.index("--read-only")
            return argv[:i] + argv[i + 1:]
        return argv
    ex = DockerExecutor(PINNED, argv_hook=weaken_candidate_only)
    pkt, _ = run("good_trim_collapse", built_repo, write_request, tmp_path, executor=ex, run_id="weak-cand")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("blocked_boundary_unverified",
                                                              "candidate_config:readonly_rootfs")
    cand = pkt["execution"]["containers"][-1]
    assert cand["role"] == "candidate" and cand["started"] is False and cand["cleanup_ok"]
    assert pkt["execution"]["candidate_executed"] is False and leftovers("weak-cand") == b""


def test_timeout_kills_and_removes_container(docker_ready, built_repo, write_request, tmp_path):
    pkt, _ = run("m2_sleep_timeout", built_repo, write_request, tmp_path, timeout_s=3, run_id="to")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("timeout", "candidate_timeout")
    cand = pkt["tests"]["candidate"]
    assert cand["timed_out"] and cand["cleanup_ok"] and cand["duration_s"] < 3 + 10
    assert leftovers("to") == b""


def test_output_cap_enforced_while_reading(docker_ready, built_repo, write_request, tmp_path):
    pkt, _ = run("m2_output_flood", built_repo, write_request, tmp_path, run_id="flood")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("execution_error",
                                                              "candidate_output_limit_exceeded:stderr")
    rec = [c for c in pkt["execution"]["containers"] if c["role"] == "candidate"][0]
    assert rec["stderr"]["bytes"] <= 65_536 and rec["cleanup_ok"]


def test_memory_and_crash_are_execution_errors(docker_ready, built_repo, write_request, tmp_path):
    pkt, _ = run("m2_memory_hog", built_repo, write_request, tmp_path, run_id="oom")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("execution_error", "candidate_oom_killed")
    pkt, _ = run("m2_import_crash", built_repo, write_request, tmp_path, run_id="crash")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("execution_error", "candidate_pytest_exit_7")


def test_baseline_that_does_not_fail_is_reported(docker_ready, built_repo, write_request, tmp_path):
    alt = copy.deepcopy(PINNED)
    alt["trusted_test"]["expected_base_failures"] = PINNED["trusted_test"]["test_ids"]  # one of them passes on base
    pkt, ex = run("good_trim_collapse", built_repo, write_request, tmp_path, pinned=alt, run_id="nofail")
    assert pkt["terminal_status"] == "base_test_did_not_fail"
    assert pkt["status_reason"] == "expected_base_failure_passed:test_issue.py::test_existing_behaviour_kept"
    assert pkt["tests"]["candidate"] is None and ex.calls_create == 2


def test_tampered_trusted_file_refused_before_containers(docker_ready, built_repo, write_request, tmp_path):
    alt = copy.deepcopy(PINNED)
    alt["runner"]["files"]["runner/run_tests.py"] = "0" * 64
    pkt, ex = run("good_trim_collapse", built_repo, write_request, tmp_path, pinned=alt, run_id="tamper")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("blocked_boundary_unverified",
                                                              "trusted_file_mismatch:runner/run_tests.py")
    assert ex.calls_create == 0


def test_docker_unavailable_blocks(docker_ready, built_repo, write_request, tmp_path):
    ex = DockerExecutor(PINNED, docker_host="unix:///nonexistent/docker.sock")
    pkt, _ = run("good_trim_collapse", built_repo, write_request, tmp_path, executor=ex, run_id="nodocker")
    assert pkt["terminal_status"] == "blocked_boundary_unverified"
    assert pkt["status_reason"].startswith("docker_unavailable") and ex.calls_create == 0


def test_canary_effects_stay_in_container(docker_ready, built_repo, write_request, tmp_path):
    CANARY.unlink(missing_ok=True)
    pkt, _ = run("known_bad_execution_canary", built_repo, write_request, tmp_path, run_id="canary")
    assert pkt["terminal_status"] == "tests_failed" and not CANARY.exists()


def test_only_git_and_docker_processes(docker_ready, built_repo, write_request, tmp_path, monkeypatch):
    seen = []
    real_popen = subprocess.Popen

    def spy_popen(argv, *a, **kw):  # every process start (subprocess.run included) goes through Popen
        seen.append(list(argv))
        return real_popen(argv, *a, **kw)
    monkeypatch.setattr(subprocess, "Popen", spy_popen)
    run("good_trim_collapse", built_repo, write_request, tmp_path, run_id="procs")
    exes = {a[0] for a in seen}
    assert exes <= {dockercmd.docker_executable(), __import__("ira.gitcmd").gitcmd.git_executable()}
    docker_subs = [a[1] for a in seen if a[0] == dockercmd.docker_executable()]
    assert set(docker_subs) <= {"version", "info", "image"} | dockercmd.CANDIDATE_PATH
    assert docker_subs.count("create") == 3
