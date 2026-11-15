"""Verdict rules for baseline and candidate runs, and the boundary-probe policy. Pure functions, no I/O.

Codex decisions (M2 amendments):
* tests_failed only when the expected tests actually report failures (exact IDs, no errors or skips).
* Missing/malformed results, OOM, output cap, process crash and other inconclusive anomalies are execution_error with
  the stage and a specific reason; no inference that the candidate caused them.
* timeout is host deadline expiry; a failed cleanup is execution_error.
* All container output (envelope, JUnit, exit codes) is candidate-influenced evidence.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .execution import ContainerResult, FSIZE_BYTES, NOFILE, REPO_MOUNT, TRUSTED_MOUNT
from .models import SchemaError, load_json_strict
from .status import Status

MAX_ENVELOPE_BYTES = 655_360
ALLOWED_RUNTIME_ENV = {"HOSTNAME", "HOME"}  # set by Docker at run time, not in Config.Env


@dataclass(frozen=True)
class Outcome:
    ok: bool                 # baseline: matches expectations; candidate: always False (status decides)
    status: Status | None
    reason: str
    pytest_exit: int | None = None
    tests: dict | None = None  # test id -> passed | failed | error | skipped


def parse_envelope(stdout: bytes):
    lines = [l for l in stdout.split(b"\n") if l.strip()]
    if not lines:
        return None, "no_envelope"
    try:
        env = load_json_strict(lines[-1], MAX_ENVELOPE_BYTES)
    except SchemaError as error:
        return None, f"envelope_{error.code}"
    if not isinstance(env, dict) or set(env) != {"envelope", "pytest_exit", "junit", "junit_truncated"} \
            or env["envelope"] != "ira.run_tests.v1" or type(env["pytest_exit"]) is not int \
            or not (env["junit"] is None or isinstance(env["junit"], str)) or type(env["junit_truncated"]) is not bool:
        return None, "envelope_malformed"
    return env, None


def parse_junit(text: str | None, truncated: bool):
    if text is None:
        return None, "junit_missing"
    if truncated:
        return None, "junit_truncated"
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.IGNORECASE):
        return None, "junit_dtd_not_allowed"
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None, "junit_malformed"
    tests = {}
    for case in root.iter("testcase"):
        cls, name = case.get("classname", ""), case.get("name", "")
        tid = f"{cls.replace('.', '/')}.py::{name}" if cls else f"::{name}"
        if tid in tests:
            return None, "junit_duplicate_test_id"
        kinds = {child.tag for child in case}
        tests[tid] = ("error" if "error" in kinds else "failed" if "failure" in kinds
                      else "skipped" if "skipped" in kinds else "passed")
    return tests, None


def _structural(stage: str, res: ContainerResult, expected_ids: list):
    """Shared checks. Returns (Outcome to stop with, or None) and (pytest_exit, tests) when conclusive."""
    def err(reason):
        return Outcome(False, Status.EXECUTION_ERROR, f"{stage}_{reason}"), None
    if res.error and not res.started:
        return err(f"infrastructure:{res.error}")
    if res.timed_out:
        if not res.cleanup_ok:
            return err("cleanup_failed_after_timeout")
        return Outcome(False, Status.TIMEOUT, f"{stage}_timeout"), None
    if not res.cleanup_ok:
        return err("cleanup_failed")
    if res.error:
        return err(f"infrastructure:{res.error}")
    if res.output_limit_exceeded:
        return err(f"output_limit_exceeded:{res.output_limit_exceeded}")
    if res.oom_killed:
        return err("oom_killed")
    if res.exit_code != 0:
        return err(f"wrapper_exit_{res.exit_code}")
    env, problem = parse_envelope(res.stdout)
    if problem:
        return err(problem)
    if env["pytest_exit"] not in (0, 1):
        return err(f"pytest_exit_{env['pytest_exit']}")
    tests, problem = parse_junit(env["junit"], env["junit_truncated"])
    if problem:
        return err(problem)
    if sorted(tests) != sorted(expected_ids):
        return err("unexpected_test_ids")
    if any(v in ("error", "skipped") for v in tests.values()):
        return err("test_error_or_skip")
    failed = any(v == "failed" for v in tests.values())
    if (env["pytest_exit"] == 1) != failed:
        return err("exit_code_inconsistent_with_junit")
    return None, (env["pytest_exit"], tests)


def decide_baseline(res: ContainerResult, expected_ids: list, expected_failures: list) -> Outcome:
    stop, data = _structural("baseline", res, expected_ids)
    if stop:
        return stop
    code, tests = data
    for tid in expected_failures:
        if tests[tid] != "failed":
            return Outcome(False, Status.BASE_TEST_DID_NOT_FAIL, f"expected_base_failure_passed:{tid}", code, tests)
    unexpected = [t for t, v in tests.items() if v == "failed" and t not in expected_failures]
    if unexpected:
        return Outcome(False, Status.EXECUTION_ERROR, "baseline_unexpected_failure", code, tests)
    return Outcome(True, None, "baseline_failed_as_expected", code, tests)


def decide_candidate(res: ContainerResult, expected_ids: list) -> Outcome:
    stop, data = _structural("candidate", res, expected_ids)
    if stop:
        return stop
    code, tests = data
    if all(v == "passed" for v in tests.values()) and code == 0:
        return Outcome(False, Status.TESTS_PASSED_AWAITING_HUMAN_REVIEW, "all_expected_tests_passed", code, tests)
    return Outcome(False, Status.TESTS_FAILED, "expected_tests_failed", code, tests)


# --------------------------------------------------------------------------- boundary probe policy
_ALLOWED_MOUNT_PREFIXES = ("/proc", "/sys", "/dev")
_ALLOWED_MOUNT_EXACT = {"/", "/tmp", REPO_MOUNT, TRUSTED_MOUNT, "/etc/hostname", "/etc/hosts", "/etc/resolv.conf",
                        "/usr/sbin/docker-init"}  # docker-init: read-only bind added by --init
_MUST_BE_READONLY = ("/", REPO_MOUNT, TRUSTED_MOUNT, "/usr/sbin/docker-init", "/etc/hostname", "/etc/hosts",
                     "/etc/resolv.conf")


def evaluate_probe(report: dict, *, image_env: list, role_env: list, memory_mb: int, pids: int) -> list[str]:
    """Return failed probe checks. `report` comes from the trusted probe script, before any candidate code runs."""
    fails = []

    def need(name, cond):
        if not cond:
            fails.append(name)

    expected_names = {e.split("=", 1)[0] for e in [*image_env, *role_env]} | ALLOWED_RUNTIME_ENV
    need("uid_gid_nobody", report.get("uid") == 65534 and report.get("gid") == 65534)
    need("env_names_allowlisted", set(report.get("env_names") or []) <= expected_names)
    need("client_canary_absent", "IRA_CLIENT_CANARY" not in (report.get("env_names") or []))
    need("only_loopback_interface", report.get("interfaces") == ["lo"])
    need("dns_blocked", str(report.get("dns_pypi", "")).startswith("failed"))
    need("tcp_blocked", all(str(v).startswith("blocked") for v in (report.get("tcp") or {}).values())
         and len(report.get("tcp") or {}) == 3)
    need("no_docker_socket", report.get("docker_socket_present") is False)
    st = report.get("status") or {}
    need("no_effective_capabilities", st.get("CapEff") == "0000000000000000")
    need("no_new_privs", st.get("NoNewPrivs") == "1")
    need("seccomp_filter", st.get("Seccomp") == "2")
    w = report.get("writable") or {}
    need("readonly_mounts", w.get("/") is False and w.get(REPO_MOUNT) is False and w.get(TRUSTED_MOUNT) is False)
    need("tmp_writable", w.get("/tmp") is True)
    mounts = {m["mount_point"]: m for m in report.get("mounts") or []}
    need("mount_points_allowlisted", all(mp in _ALLOWED_MOUNT_EXACT or mp.startswith(_ALLOWED_MOUNT_PREFIXES)
                                         for mp in mounts))
    need("required_mounts_present", {"/", "/tmp", REPO_MOUNT, TRUSTED_MOUNT} <= set(mounts))
    need("bind_mounts_readonly", all("ro" in mounts[mp].get("options", "").split(",")
                                     for mp in _MUST_BE_READONLY if mp in mounts))
    need("tmp_is_tmpfs", mounts.get("/tmp", {}).get("fstype") == "tmpfs")
    cg = report.get("cgroup") or {}
    need("memory_limit", str(cg.get("memory_limit")) == str(memory_mb * 1024 * 1024))
    need("pids_limit", str(cg.get("pids_max")) == str(pids))
    need("rlimit_fsize", report.get("rlimit_fsize") == FSIZE_BYTES)
    need("rlimit_nofile", report.get("rlimit_nofile") == NOFILE)
    need("no_credential_paths", report.get("credential_paths_present") == [])
    return fails
