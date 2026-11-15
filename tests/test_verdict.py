"""Pure verdict and probe-policy rules (no Docker)."""
import json

import pytest

from conftest import PINNED
from ira.execution import ContainerResult
from ira.status import Status
from ira.verdict import decide_baseline, decide_candidate, evaluate_probe, parse_junit

IDS = PINNED["trusted_test"]["test_ids"]
EXP_FAIL = PINNED["trusted_test"]["expected_base_failures"]


def junit(outcomes: dict) -> str:
    cases = []
    for tid, outcome in outcomes.items():
        mod, name = tid.split(".py::")
        inner = {"passed": "", "failed": "<failure message='x'/>", "error": "<error message='x'/>",
                 "skipped": "<skipped message='x'/>"}[outcome]
        cases.append(f'<testcase classname="{mod}" name="{name}">{inner}</testcase>')
    return f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{"".join(cases)}</testsuite></testsuites>'


def result(pytest_exit=1, outcomes=None, *, junit_text=None, stdout=None, **kw):
    if stdout is None:
        text = junit_text if junit_text is not None else junit(outcomes)
        stdout = (json.dumps({"envelope": "ira.run_tests.v1", "pytest_exit": pytest_exit, "junit": text,
                              "junit_truncated": False}) + "\n").encode()
    base = dict(role="x", name="n", argv=[], started=True, exit_code=0, oom_killed=False, cleanup_ok=True,
                stdout=stdout)
    base.update(kw)
    return ContainerResult(**base)


BASE_OK = {IDS[0]: "failed", IDS[1]: "failed", IDS[2]: "passed"}
ALL_PASS = {i: "passed" for i in IDS}


def test_baseline_as_expected():
    o = decide_baseline(result(1, BASE_OK), IDS, EXP_FAIL)
    assert o.ok and o.reason == "baseline_failed_as_expected"


def test_baseline_did_not_fail():
    o = decide_baseline(result(1, {**BASE_OK, IDS[1]: "passed"}), IDS, EXP_FAIL)
    assert (o.status, o.reason) == (Status.BASE_TEST_DID_NOT_FAIL, f"expected_base_failure_passed:{IDS[1]}")
    assert decide_baseline(result(0, ALL_PASS), IDS, EXP_FAIL).status is Status.BASE_TEST_DID_NOT_FAIL


@pytest.mark.parametrize("kw,reason", [
    (dict(outcomes={**BASE_OK, IDS[2]: "failed"}), "baseline_unexpected_failure"),
    (dict(pytest_exit=2, outcomes=BASE_OK), "baseline_pytest_exit_2"),
    (dict(stdout=b""), "baseline_no_envelope"),
    (dict(stdout=b"not json\n"), "baseline_envelope_invalid_json"),
    (dict(stdout=b'{"envelope": "ira.run_tests.v1"}\n'), "baseline_envelope_malformed"),
    (dict(junit_text=None, stdout=(json.dumps({"envelope": "ira.run_tests.v1", "pytest_exit": 1, "junit": None,
                                                "junit_truncated": False}) + "\n").encode()), "baseline_junit_missing"),
    (dict(junit_text="<testsuites><testcase"), "baseline_junit_malformed"),
    (dict(junit_text='<!DOCTYPE x [<!ENTITY a "b">]><testsuites/>'), "baseline_junit_dtd_not_allowed"),
    (dict(outcomes={IDS[0]: "failed"}), "baseline_unexpected_test_ids"),
    (dict(outcomes={**BASE_OK, "test_issue.py::extra": "passed"}), "baseline_unexpected_test_ids"),
    (dict(outcomes={**BASE_OK, IDS[2]: "skipped"}), "baseline_test_error_or_skip"),
    (dict(pytest_exit=0, outcomes=BASE_OK), "baseline_exit_code_inconsistent_with_junit"),
    (dict(outcomes=BASE_OK, oom_killed=True), "baseline_oom_killed"),
    (dict(outcomes=BASE_OK, output_limit_exceeded="stderr"), "baseline_output_limit_exceeded:stderr"),
    (dict(outcomes=BASE_OK, exit_code=137), "baseline_wrapper_exit_137"),
    (dict(outcomes=BASE_OK, cleanup_ok=False), "baseline_cleanup_failed"),
    (dict(outcomes=BASE_OK, started=False, error="create_failed:x"), "baseline_infrastructure:create_failed:x"),
])
def test_baseline_anomalies_are_execution_errors(kw, reason):
    o = decide_baseline(result(**{"pytest_exit": 1, **kw}), IDS, EXP_FAIL)
    assert (o.status, o.reason) == (Status.EXECUTION_ERROR, reason)


def test_timeout_and_cleanup_outcome():
    assert decide_baseline(result(1, BASE_OK, timed_out=True), IDS, EXP_FAIL).status is Status.TIMEOUT
    o = decide_candidate(result(1, BASE_OK, timed_out=True, cleanup_ok=False), IDS)
    assert (o.status, o.reason) == (Status.EXECUTION_ERROR, "candidate_cleanup_failed_after_timeout")


def test_candidate_pass_and_fail():
    assert decide_candidate(result(0, ALL_PASS), IDS).status is Status.TESTS_PASSED_AWAITING_HUMAN_REVIEW
    o = decide_candidate(result(1, {**ALL_PASS, IDS[0]: "failed"}), IDS)
    assert (o.status, o.reason) == (Status.TESTS_FAILED, "expected_tests_failed")


@pytest.mark.parametrize("kw", [dict(outcomes={**ALL_PASS, IDS[0]: "error"}), dict(pytest_exit=2, outcomes=ALL_PASS),
                                dict(stdout=b""), dict(outcomes=ALL_PASS, oom_killed=True),
                                dict(outcomes=ALL_PASS, output_limit_exceeded="stdout"),
                                dict(outcomes={IDS[0]: "passed"})])
def test_candidate_inconclusive_is_execution_error_not_tests_failed(kw):
    o = decide_candidate(result(**{"pytest_exit": 0, **kw}), IDS)
    assert o.status is Status.EXECUTION_ERROR and o.reason.startswith("candidate_")


def test_junit_duplicate_ids_rejected():
    text = junit({IDS[0]: "passed"}).replace("</testsuite>", f'<testcase classname="test_issue" name="{IDS[0].split("::")[1]}"/></testsuite>')
    assert parse_junit(text, False) == (None, "junit_duplicate_test_id")


# --------------------------------------------------------------------------- probe policy
IMAGE_ENV = ["PATH=/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8", "GPG_KEY=x", "PYTHON_VERSION=3.12.16",
             "PYTHON_SHA256=y"]
ROLE_ENV = ["PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "IRA_ROLE=probe"]


def good_report():
    ro = "ro,relatime"
    return {"probe": "ira.probe.v1", "uid": 65534, "gid": 65534,
            "env_names": ["GPG_KEY", "HOME", "HOSTNAME", "IRA_ROLE", "LANG", "PATH", "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
                          "PYTHON_SHA256", "PYTHON_VERSION"],
            "interfaces": ["lo"], "dns_pypi": "failed:gaierror",
            "tcp": {"1.1.1.1:443": "blocked:OSError", "169.254.169.254:80": "blocked:OSError",
                    "172.17.0.1:80": "blocked:OSError"},
            "mounts": [{"mount_point": "/", "options": ro, "fstype": "overlay"},
                       {"mount_point": "/tmp", "options": "rw,nosuid", "fstype": "tmpfs"},
                       {"mount_point": "/work/repo", "options": ro, "fstype": "ext4"},
                       {"mount_point": "/trusted", "options": ro, "fstype": "ext4"},
                       {"mount_point": "/proc", "options": "rw", "fstype": "proc"},
                       {"mount_point": "/usr/sbin/docker-init", "options": ro, "fstype": "ext4"}],
            "docker_socket_present": False,
            "status": {"CapEff": "0000000000000000", "NoNewPrivs": "1", "Seccomp": "2"},
            "writable": {"/": False, "/work/repo": False, "/trusted": False, "/tmp": True},
            "cgroup": {"version": 1, "memory_limit": str(256 * 1024 * 1024), "pids_max": "64"},
            "rlimit_fsize": 33554432, "rlimit_nofile": 256, "credential_paths_present": []}


def probe_fails(report):
    return evaluate_probe(report, image_env=IMAGE_ENV, role_env=ROLE_ENV, memory_mb=256, pids=64)


def test_good_probe_passes():
    assert probe_fails(good_report()) == []


@pytest.mark.parametrize("mutate,check", [
    (lambda r: r.update(uid=0), "uid_gid_nobody"),
    (lambda r: r["env_names"].append("AWS_SECRET_ACCESS_KEY"), "env_names_allowlisted"),
    (lambda r: r["env_names"].append("IRA_CLIENT_CANARY"), "client_canary_absent"),
    (lambda r: r.update(interfaces=["eth0", "lo"]), "only_loopback_interface"),
    (lambda r: r.update(dns_pypi="resolved"), "dns_blocked"),
    (lambda r: r["tcp"].update({"1.1.1.1:443": "connected"}), "tcp_blocked"),
    (lambda r: r.update(docker_socket_present=True), "no_docker_socket"),
    (lambda r: r["status"].update(CapEff="00000000a80425fb"), "no_effective_capabilities"),
    (lambda r: r["status"].update(NoNewPrivs="0"), "no_new_privs"),
    (lambda r: r["status"].update(Seccomp="0"), "seccomp_filter"),
    (lambda r: r["writable"].update({"/work/repo": True}), "readonly_mounts"),
    (lambda r: r["mounts"].append({"mount_point": "/home/runner", "options": "rw", "fstype": "ext4"}),
     "mount_points_allowlisted"),
    (lambda r: r["mounts"][2].update(options="rw,relatime"), "bind_mounts_readonly"),
    (lambda r: r["cgroup"].update(memory_limit="9223372036854771712"), "memory_limit"),
    (lambda r: r["cgroup"].update(pids_max="max"), "pids_limit"),
    (lambda r: r.update(rlimit_nofile=1048576), "rlimit_nofile"),
    (lambda r: r.update(credential_paths_present=["/root/.aws"]), "no_credential_paths"),
])
def test_probe_failures_detected(mutate, check):
    r = good_report()
    mutate(r)
    assert check in probe_fails(r)
