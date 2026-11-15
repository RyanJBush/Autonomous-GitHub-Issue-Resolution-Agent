"""M1 amendment: the candidate-handling path invokes only its fixed git operations and executes no candidate content."""
import subprocess
import sys

import pytest

from conftest import CANARY
from ira import gitcmd, runner
from ira.adapters import load_cases

ACCEPTED = [c["id"] for c in load_cases() if c["expected_status"] == "blocked_boundary_unverified"]
ALL = [c["id"] for c in load_cases()]
EXPECTED = {c["id"]: c["expected_status"] for c in load_cases()}


class Calls(list):
    """Recorded git calls; `violations` survives even if the runner's catch-all swallows the raised error."""
    violations: list


@pytest.fixture
def recorder(monkeypatch):
    calls = Calls()
    calls.violations = []
    real_run, real_popen = subprocess.run, subprocess.Popen
    inside = [False]

    def spy_run(argv, *a, **kw):
        calls.append({"argv": list(argv), "stdin": kw.get("input"), "env": kw.get("env")})
        inside[0] = True
        try:
            return real_run(argv, *a, **kw)
        finally:
            inside[0] = False

    def forbid(*a, **kw):
        calls.violations.append(repr(a[:1])[:200])
        raise AssertionError(f"unexpected process start: {a[:1]}")

    def guarded_popen(*a, **kw):  # only the recorded gitcmd.run_git call may create a process
        if not inside[0]:
            forbid(*a, **kw)
        return real_popen(*a, **kw)

    monkeypatch.setattr(gitcmd.subprocess, "run", spy_run)
    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
    monkeypatch.setattr(subprocess, "call", forbid)
    monkeypatch.setattr(subprocess, "check_output", forbid)
    import os
    monkeypatch.setattr(os, "system", forbid)
    monkeypatch.setattr(os, "execv", forbid)
    monkeypatch.setattr(os, "posix_spawn", forbid, raising=False)
    return calls


def _subcommand(argv):
    i = 1
    while argv[i] == "-c":
        i += 2
    return argv[i]


@pytest.mark.parametrize("case", ALL)
def test_only_fixed_git_operations(case, built_repo, write_request, tmp_path, recorder):
    CANARY.unlink(missing_ok=True)
    modules_before = set(sys.modules)
    pkt, _ = runner.execute(write_request(case, built_repo), tmp_path / "o", work_root=tmp_path / "w")
    assert not recorder.violations, recorder.violations
    assert pkt["terminal_status"] == EXPECTED[case]
    git = gitcmd.git_executable()
    response = (tmp_path / "o" / case / "packet.json").read_text()
    seen_response = False
    for call in recorder:
        argv = call["argv"]
        assert argv[0] == git, "only the trusted git executable may be started"
        assert argv[1:1 + 2 * len(gitcmd.CONFIG_OVERRIDES)] == [x for o in gitcmd.CONFIG_OVERRIDES for x in ("-c", o)]
        assert _subcommand(argv) in gitcmd.ALLOWED_SUBCOMMANDS
        assert set(call["env"]) <= set(gitcmd.controlled_env()) | {"GIT_DIR", "GIT_WORK_TREE"}
        if call["stdin"]:
            seen_response = True
            assert _subcommand(argv) == "apply"  # candidate text reaches git only on stdin, only for apply
    if pkt["patch"]:
        diff = pkt["patch"]["diff"]
        for call in recorder:
            assert all(diff not in a for a in call["argv"]), "candidate diff must never be a command-line argument"
        assert seen_response
    # Calls after base verification belong to the candidate-handling path; they use the restricted set only.
    after_base = recorder[next((i for i, c in enumerate(recorder) if _subcommand(c["argv"]) == "cat-file"), 0) + 1:]
    for call in after_base:
        sub = _subcommand(call["argv"])
        assert sub in gitcmd.CANDIDATE_PATH_SUBCOMMANDS | {"rev-parse", "status"}  # last two: provenance of this repo
    assert not CANARY.exists(), "candidate code must never run"
    new_modules = set(sys.modules) - modules_before
    assert not any(m == "labels" or m.endswith(".labels") for m in new_modules)
    assert response  # packet written


@pytest.mark.parametrize("case", ACCEPTED)
def test_candidate_path_subcommand_sequence_is_fixed(case, built_repo, write_request, tmp_path, recorder):
    runner.execute(write_request(case, built_repo), tmp_path / "o", work_root=tmp_path / "w")
    assert not recorder.violations
    subs = [_subcommand(c["argv"]) for c in recorder]
    start = subs.index("cat-file") + 1
    assert subs[start:] == ["init", "add", "apply", "apply", "diff", "diff", "status", "rev-parse", "status"]
