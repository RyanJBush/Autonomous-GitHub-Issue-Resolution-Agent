"""Milestone 2 executor: probe -> baseline -> candidate, for authorized trusted synthetic fixtures only.

Passing probes show that this run's containers had the observed properties on this host. They are not proof that
arbitrary model-generated code is safely isolated (shared kernel, rootful daemon, in-process tampering).
"""
from __future__ import annotations

import json
import platform
from dataclasses import dataclass, field

from . import dockercmd, execution
from .status import Status
from .verdict import decide_baseline, decide_candidate, evaluate_probe


@dataclass
class ExecutionReport:
    status: Status
    reason: str
    candidate_executed: bool = False
    host: dict = field(default_factory=dict)
    image: dict = field(default_factory=dict)
    probe: dict | None = None
    containers: list = field(default_factory=list)
    baseline: dict | None = None
    candidate: dict | None = None


IMAGE_TRUST_NOTE = ("The image id and the self-declared ira.inputs_sha256 label support traceability only. They do not "
                    "independently prove the image was built from the pinned inputs; image ids are not reproducible "
                    "across builds.")


def runner_image_tag(pinned: dict) -> str:
    return f"ira-runner:{pinned['runner']['image_inputs_sha256'][:12]}"


class DockerExecutor:
    def __init__(self, pinned: dict, *, docker_host: str | None = None, argv_hook=None):
        self.pinned, self.docker_host, self.argv_hook = pinned, docker_host, argv_hook
        self.calls_create = 0

    def _docker(self, args, **kw):
        return dockercmd.run_docker(args, docker_host=self.docker_host, **kw)

    def run(self, *, run_id: str, repo_path: str, base_commit: str, allowlisted_file: str, new_content: bytes,
            limits: execution.Limits) -> ExecutionReport:
        rep = ExecutionReport(Status.BLOCKED_BOUNDARY_UNVERIFIED, "not_started")
        try:
            server = self._docker(["version", "--format", "{{.Server.Version}}"], timeout=20).stdout.decode().strip()
            info = json.loads(self._docker(["info", "--format", "{{json .}}"], timeout=20).stdout)
        except (dockercmd.DockerError, ValueError) as error:
            rep.reason = f"docker_unavailable:{str(error)[:120]}"
            return rep
        rep.host = {"docker_server_version": server, "cgroup_version": info.get("CgroupVersion"),
                    "default_runtime": info.get("DefaultRuntime"), "kernel": platform.release(),
                    "os": info.get("OperatingSystem"), "rootless": "rootless" in json.dumps(info.get("SecurityOptions"))}
        tag = runner_image_tag(self.pinned)
        try:
            img = json.loads(self._docker(["image", "inspect", tag]).stdout)[0]
        except (dockercmd.DockerError, ValueError, IndexError):
            rep.reason = "runner_image_missing"
            return rep
        labels = (img.get("Config") or {}).get("Labels") or {}
        if labels.get("ira.inputs_sha256") != self.pinned["runner"]["image_inputs_sha256"]:
            rep.reason = "runner_image_inputs_mismatch"
            return rep
        image_id, image_env = img["Id"], list((img.get("Config") or {}).get("Env") or [])
        rep.image = {"tag": tag, "id": image_id, "inputs_sha256": labels["ira.inputs_sha256"],
                     "base_image": self.pinned["runner"]["base_image"],
                     "trust_note": IMAGE_TRUST_NOTE}

        work = execution.new_work_root()
        try:
            try:
                trusted = execution.stage_trusted(work, self.pinned)
                base_tree = execution.materialize_tree(work, "base", repo_path, base_commit)
                cand_tree = execution.materialize_tree(work, "candidate", repo_path, base_commit,
                                                       {allowlisted_file: new_content})
            except execution.ExecutionSetupError as error:
                rep.reason = f"{error.code}:{error.detail}"[:160]
                return rep
            common = dict(run_id=run_id, image_id=image_id, image_env=image_env, limits=limits, trusted=trusted,
                          docker_host=self.docker_host, argv_hook=self.argv_hook)
            tt = self.pinned["trusted_test"]

            # 1. Probe: same flags and mounts as the test containers; trusted probe script only.
            probe = self._run("probe", base_tree, execution.PROBE_COMMAND, execution.PROBE_TIMEOUT_S, common, rep)
            if self._gate(probe, rep):
                return rep
            report = None
            if probe.exit_code == 0 and not probe.timed_out and not probe.output_limit_exceeded:
                try:
                    report = json.loads(probe.stdout.decode("utf-8").strip().splitlines()[-1])
                except (ValueError, IndexError, UnicodeDecodeError):
                    report = None
            if not isinstance(report, dict) or report.get("probe") != "ira.probe.v1":
                rep.reason = "probe_failed:no_report"
                return rep
            fails = evaluate_probe(report, image_env=image_env, role_env=[*execution.EXTRA_ENV, "IRA_ROLE=probe"],
                                   memory_mb=limits.memory_mb, pids=limits.pids)
            safe_report = {k: v for k, v in report.items() if k != "env_names"}  # amendment 6: names only on failure
            if "env_names_allowlisted" in fails or "client_canary_absent" in fails:
                safe_report["env_names"] = report.get("env_names")
            rep.probe = {"passed": not fails, "failed_checks": fails, "observations": safe_report}
            if fails:
                rep.reason = "probe_check:" + ",".join(fails)
                return rep

            # 2. Baseline on the pinned base tree.
            base = self._run("baseline", base_tree, execution.TEST_COMMAND, limits.timeout_s, common, rep)
            if self._gate(base, rep):
                return rep
            bo = decide_baseline(base, tt["test_ids"], tt["expected_base_failures"])
            rep.baseline = self._record(base, bo)
            if not bo.ok:
                rep.status, rep.reason = bo.status, bo.reason
                return rep

            # 3. Candidate on the tree built only from the verified base and the verified patch result.
            cand = self._run("candidate", cand_tree, execution.TEST_COMMAND, limits.timeout_s, common, rep)
            if self._gate(cand, rep):
                return rep
            rep.candidate_executed = cand.started
            co = decide_candidate(cand, tt["test_ids"])
            rep.candidate = self._record(cand, co)
            rep.status, rep.reason = co.status, co.reason
            return rep
        finally:
            execution.remove_work(work)

    @staticmethod
    def _gate(res, rep) -> bool:
        """Applied to every created container, in this order. Returns True when the run must stop here.

        1. Cleanup failure takes precedence (execution_error), keeping any configuration failure or timeout in the
           reason; the full container record (config failures and cleanup detail) is already in rep.containers.
        2. A configuration failure on a cleaned-up container blocks the run (blocked_boundary_unverified).
        """
        if not res.cleanup_ok:
            reason = f"{res.role}_cleanup_failed"
            if res.config_failures:
                reason += "_after_config_failure:" + ",".join(res.config_failures)
            elif res.timed_out:
                reason += "_after_timeout"
            rep.status, rep.reason = Status.EXECUTION_ERROR, reason
            return True
        if res.config_failures:
            rep.status, rep.reason = Status.BLOCKED_BOUNDARY_UNVERIFIED, f"{res.role}_config:" + ",".join(res.config_failures)
            return True
        return False

    def _run(self, role, tree, command, deadline, common, rep):
        self.calls_create += 1
        res = execution.run_container(role=role, tree=tree, command=command, deadline_s=deadline, **common)
        rep.containers.append(execution.container_record(res))
        return res

    @staticmethod
    def _record(res, outcome) -> dict:
        return {"stage": res.role, "container": res.name, "started": res.started, "timed_out": res.timed_out,
                "cleanup_ok": res.cleanup_ok, "cleanup_detail": res.cleanup_detail, "exit_code": res.exit_code,
                "oom_killed": res.oom_killed, "output_limit_exceeded": res.output_limit_exceeded,
                "duration_s": res.duration_s, "pytest_exit": outcome.pytest_exit, "tests": outcome.tests,
                "verdict": outcome.status.value if outcome.status else "as_expected", "reason": outcome.reason,
                "evidence_note": "JUnit, exit codes and output are candidate-influenced evidence"}
