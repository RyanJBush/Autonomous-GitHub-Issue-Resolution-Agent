"""Paid-call approval packet: bindings, operator approval and refusal before any adapter exists (offline)."""
import json

import pytest

from conftest import ROOT
from ira import approval, cli, live_adapter
from ira.evaluate import make_request
from ira.gitcmd import GitResult
from test_live_adapter import LIVE

COMMIT = "a" * 40


@pytest.fixture
def fake_git(monkeypatch):
    """The project's own git state, controlled per test (the real tree may be dirty during development)."""
    state = {"head": COMMIT, "dirty": b"", "ancestor": 0, "changed": b""}

    def fake(*args, check=True):
        if args[0] == "rev-parse":
            return GitResult(args, 0, (state["head"] + "\n").encode(), b"")
        if args[0] == "status":
            return GitResult(args, 0, state["dirty"], b"")
        if args[0] == "merge-base":
            return GitResult(args, state["ancestor"], b"", b"")
        if args[0] == "diff":
            return GitResult(args, 0, state["changed"], b"")
        raise AssertionError(args)
    monkeypatch.setattr(approval, "_git", fake)
    return state


@pytest.fixture
def live_req(tmp_path, built_repo):
    req = make_request("unused", built_repo, "live-approval-test")
    req["adapter"] = dict(LIVE)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(req))
    return path


def approve(path, by="operator"):
    pkt = json.loads(path.read_text())
    pkt["operator_approval"] = {"approved": True, "approved_by": by, "approved_at": "2026-11-15T12:00:00Z",
                                "statement": "I approve exactly one request with these bindings."}
    path.write_text(json.dumps(pkt))


def test_prepare_binds_everything_and_starts_unapproved(fake_git, live_req, tmp_path):
    out = tmp_path / "approval.json"
    pkt = approval.prepare(live_req, out)
    b = pkt["binds"]
    assert pkt["status"] == "awaiting_operator_approval" and pkt["operator_approval"]["approved"] is False
    assert pkt["code_commit"] == COMMIT and b["procedure_path"] == "docs/LIVE_CALL_PROCEDURE.md"
    assert b["procedure_sha256"] and b["model"] == "claude-sonnet-5-5" and b["requests"] == 1
    assert len(pkt["approval_id"]) == 32 and "NOT enforced globally" in pkt["single_use"]["scope"]
    assert b["max_retries"] == 0 and b["tools"] is None and b["fallbacks"] is None
    assert b["estimate"]["label"].startswith("ESTIMATE") and b["estimate_threshold_usd"] == "0.25"
    assert "executing the response or any code derived from it" in pkt["what_approval_does_not_authorize"]


def test_unapproved_packet_refused(fake_git, live_req, tmp_path):
    out = tmp_path / "approval.json"
    approval.prepare(live_req, out)
    with pytest.raises(approval.ApprovalError) as info:
        approval.verify(out, live_req)
    assert info.value.code == "operator_approval_missing"
    approve(out)
    assert approval.verify(out, live_req)["operator_approval"]["approved"] is True


@pytest.mark.parametrize("field,value", [("model", "claude-opus-5-5"), ("max_tokens", 4000),
                                         ("procedure_sha256", "0" * 64), ("prompt_sha256", "0" * 64),
                                         ("estimate_threshold_usd", "9.99")])
def test_any_binding_change_refused(fake_git, live_req, tmp_path, field, value):
    out = tmp_path / "approval.json"
    approval.prepare(live_req, out)
    approve(out)
    pkt = json.loads(out.read_text())
    pkt["binds"][field] = value
    out.write_text(json.dumps(pkt))
    with pytest.raises(approval.ApprovalError) as info:
        approval.verify(out, live_req)
    assert info.value.code == f"binding_mismatch:{field}"


def test_request_edited_after_approval_refused(fake_git, live_req, tmp_path):
    out = tmp_path / "approval.json"
    approval.prepare(live_req, out)
    approve(out)
    req = json.loads(live_req.read_text())
    req["adapter"]["effort"] = "high"
    live_req.write_text(json.dumps(req))
    with pytest.raises(approval.ApprovalError) as info:
        approval.verify(out, live_req)
    assert info.value.code == "binding_mismatch:request_sha256"


def test_code_commit_rules(fake_git, live_req, tmp_path):
    out = tmp_path / "approval.json"
    approval.prepare(live_req, out)
    approve(out)
    fake_git["head"], fake_git["ancestor"] = "b" * 40, 1
    with pytest.raises(approval.ApprovalError) as info:
        approval.verify(out, live_req)
    assert info.value.code == "code_commit_not_ancestor"
    fake_git["ancestor"], fake_git["changed"] = 0, b"demo/live/approval.json\n"
    assert approval.verify(out, live_req)  # only the committed proposal changed: allowed
    fake_git["changed"] = b"demo/live/approval.json\nira/live_adapter.py\n"
    with pytest.raises(approval.ApprovalError) as info:
        approval.verify(out, live_req)
    assert info.value.code == "code_changed_since_approval"
    fake_git["head"], fake_git["dirty"] = COMMIT, b" M ira/runner.py\n"
    with pytest.raises(approval.ApprovalError) as info:
        approval.verify(out, live_req)
    assert info.value.code == "project_tree_dirty"


def test_prepare_refuses_dirty_tree_non_live_and_over_cap(fake_git, tmp_path, built_repo, live_req):
    fake_git["dirty"] = b"?? x\n"
    with pytest.raises(approval.ApprovalError) as info:
        approval.prepare(live_req, tmp_path / "a.json")
    assert info.value.code == "project_tree_dirty"
    fake_git["dirty"] = b""
    syn = tmp_path / "syn.json"
    syn.write_text(json.dumps(make_request("good_trim_collapse", built_repo, "syn")))
    with pytest.raises(approval.ApprovalError) as info:
        approval.prepare(syn, tmp_path / "b.json")
    assert info.value.code == "request_is_not_live"
    req = json.loads(live_req.read_text())
    req["adapter"]["estimate_threshold_cents"] = 1
    live_req.write_text(json.dumps(req))
    with pytest.raises(approval.ApprovalError) as info:
        approval.prepare(live_req, tmp_path / "c.json")
    assert info.value.code == "estimate_exceeds_threshold"


@pytest.fixture
def no_adapter(monkeypatch):
    built = []

    class Boom:
        def __init__(self, *a, **k):
            built.append(1)
            raise AssertionError("adapter must not be constructed")
    monkeypatch.setattr(live_adapter, "LiveAdapter", Boom)
    return built


def test_cli_refuses_before_any_adapter(fake_git, live_req, tmp_path, no_adapter, capsys, tty):
    out = tmp_path / "approval.json"
    approval.prepare(live_req, out)
    runs = tmp_path / "runs"
    assert cli.main(["run", "--request", str(live_req), "--out", str(runs)]) == 2           # live request, no --live
    assert cli.main(["run", "--live", "--request", str(live_req), "--out", str(runs)]) == 2  # no packet
    assert cli.main(["run", "--live", "--request", str(live_req), "--out", str(runs),
                     "--approval-packet", str(out), "--api-key-prompt"]) == 2                # not approved
    synthetic = ROOT / "demo/request.example.json"
    assert cli.main(["run", "--live", "--request", str(synthetic), "--out", str(runs)]) == 2  # --live on synthetic
    assert no_adapter == [] and not runs.exists()
    assert "operator_approval_missing" in capsys.readouterr().err


def test_committed_first_call_request_is_valid_and_bounded():
    from ira.models import load_request
    req = load_request((ROOT / "demo/live/request.json").read_bytes())
    a = req.adapter
    assert (a.kind, a.model, a.max_tokens, a.effort, a.estimate_threshold_cents) == ("live_model", "claude-sonnet-5-5", 8000,
                                                                           "medium", 25)



@pytest.mark.parametrize("status", [b"?? notes.txt\n", b"!! .venv/\n", b"!! ira/__pycache__/\n", b" M README.md\n"])
def test_untracked_and_ignored_files_fail_closed(fake_git, live_req, tmp_path, status):
    out = tmp_path / "approval.json"
    approval.prepare(live_req, out)
    approve(out)
    fake_git["dirty"] = status
    with pytest.raises(approval.ApprovalError) as info:
        approval.verify(out, live_req)
    assert info.value.code == "project_tree_dirty"


def test_strict_status_command_includes_untracked_and_ignored():
    assert approval.STRICT_STATUS == ("status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching")


def test_malformed_approval_id_refused(fake_git, live_req, tmp_path):
    out = tmp_path / "approval.json"
    approval.prepare(live_req, out)
    approve(out)
    pkt = json.loads(out.read_text())
    pkt["approval_id"] = "reused"
    out.write_text(json.dumps(pkt))
    with pytest.raises(approval.ApprovalError) as info:
        approval.verify(out, live_req)
    assert info.value.code == "approval_packet_malformed"


def test_cli_refuses_out_inside_checkout_and_used_approvals(fake_git, live_req, tmp_path, no_adapter, capsys, tty):
    from ira.single_use import SingleUseLedger
    out = tmp_path / "approval.json"
    pkt = approval.prepare(live_req, out)
    approve(out)
    inside = ROOT / "out" / "live-should-not-exist"
    assert cli.main(["run", "--live", "--request", str(live_req), "--out", str(inside),
                     "--approval-packet", str(out), "--spent-ledger", str(tmp_path / "l")]) == 2
    assert "outside the checkout" in capsys.readouterr().err and not inside.exists()
    ledger = SingleUseLedger(tmp_path / "l")
    ledger.claim(pkt["approval_id"], request_sha256="0" * 64, run_id="earlier")
    assert cli.main(["run", "--live", "--request", str(live_req), "--out", str(tmp_path / "runs"),
                     "--approval-packet", str(out), "--spent-ledger", str(tmp_path / "l"), "--api-key-prompt"]) == 2
    assert "approval_already_used" in capsys.readouterr().err
    assert no_adapter == [] and not (tmp_path / "runs").exists()


def test_no_live_workflow_and_committed_proposal_is_unapproved():
    assert not (ROOT / ".github/workflows/live-proposal.yml").exists()
    proposal = ROOT / approval.PROPOSAL_PATH
    if proposal.exists():
        pkt = json.loads(proposal.read_text())
        assert pkt["operator_approval"]["approved"] is False and pkt["status"] == "awaiting_operator_approval"
        assert len(pkt["approval_id"]) == 32 and pkt["binds"]["procedure_path"] == approval.PROCEDURE_PATH


# --------------------------------------------------------------------------- hidden key prompt (Codex review round 3)
KEY_SENTINEL = "sk-ant-FAKE_SECRET_SENTINEL_KEY_0001"


@pytest.fixture
def tty(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def approved(fake_git, live_req, tmp_path):
    out = tmp_path / "approval.json"
    approval.prepare(live_req, out)
    approve(out)
    return out


def _live_args(live_req, packet, tmp_path, *extra):
    return ["run", "--live", "--request", str(live_req), "--approval-packet", str(packet),
            "--out", str(tmp_path / "runs"), "--spent-ledger", str(tmp_path / "ledger"), *extra]


def test_prompted_key_goes_only_to_the_client(approved, live_req, tmp_path, tty, monkeypatch):
    import getpass
    import os
    from test_live_adapter import FakeClient, message
    seen = {}

    def fake_factory(spec, api_key=None):
        seen.update(api_key=api_key, in_env=KEY_SENTINEL in os.environ.values())
        return FakeClient(message())
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": KEY_SENTINEL + "\n")
    monkeypatch.setattr(live_adapter, "default_client_factory", fake_factory)
    assert cli.main(_live_args(live_req, approved, tmp_path, "--api-key-prompt")) == 0
    assert seen == {"api_key": KEY_SENTINEL, "in_env": False}
    files = [p for p in tmp_path.rglob("*") if p.is_file() and p.name != "approval.json"]
    assert files and all(KEY_SENTINEL not in p.read_text(errors="replace") for p in files)
    assert len(list((tmp_path / "ledger").glob("*.spent"))) == 1


@pytest.mark.parametrize("case", ["no_flag", "env_key", "env_token", "no_tty", "bad_format"])
def test_key_entry_refusals_happen_before_any_request(approved, live_req, tmp_path, tty, monkeypatch, case,
                                                      no_adapter, capsys):
    import getpass
    import sys
    calls = []
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": calls.append(1) or "not-a-key")
    args = _live_args(live_req, approved, tmp_path, *([] if case == "no_flag" else ["--api-key-prompt"]))
    if case == "env_key":
        monkeypatch.setenv("ANTHROPIC_API_KEY", KEY_SENTINEL)
    if case == "env_token":
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", KEY_SENTINEL)
    if case == "no_tty":
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert cli.main(args) == 2
    err = capsys.readouterr().err
    assert KEY_SENTINEL not in err and "not-a-key" not in err
    assert calls == ([1] if case == "bad_format" else [])  # the prompt is shown only when everything else passed
    assert no_adapter == [] and not (tmp_path / "runs").exists() and not list(tmp_path.glob("ledger/*.spent"))


def test_provider_host_is_pinned_even_if_environment_says_otherwise(monkeypatch):
    from ira.models import load_request
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://attacker.invalid")
    spec = load_request((ROOT / "demo/live/request.json").read_bytes()).adapter
    client = live_adapter.default_client_factory(spec, api_key="sk-ant-dummy-offline")
    assert str(client.base_url).rstrip("/") == "https://api.anthropic.com" and client.max_retries == 0


# --------------------------------------------------------------------------- operator environment check
def _fake_sys(monkeypatch, *, prefix="/home/op/.venvs/ira-live", bytecode=True, tty=True):
    from types import SimpleNamespace
    import sys as real
    from ira import operator_check
    monkeypatch.setattr(operator_check, "sys", SimpleNamespace(
        platform="linux", version_info=real.version_info, prefix=prefix,
        flags=SimpleNamespace(dont_write_bytecode=bytecode), stdin=SimpleNamespace(isatty=lambda: tty)))


@pytest.fixture
def temp_proposal(fake_git, live_req, tmp_path, monkeypatch):
    """An unapproved proposal outside the repository, so these tests do not depend on committed state."""
    out = tmp_path / "proposal.json"
    pkt = approval.prepare(live_req, out)
    monkeypatch.setattr(approval, "PROPOSAL_PATH", str(out))
    return pkt


def test_operator_check_ready(fake_git, live_req, tmp_path, monkeypatch, temp_proposal):
    from ira.operator_check import run_checks
    _fake_sys(monkeypatch)
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    rows = run_checks(str(live_req), str(tmp_path / "ledger"))
    assert [r["check"] for r in rows if not r["ok"]] == []


@pytest.mark.parametrize("problem,check", [
    ("prefix", "venv_outside_checkout"), ("bytecode", "no_bytecode_writes"), ("tty", "interactive_terminal"),
    ("env", "no_credential_environment_variables"), ("dirty", "checkout_strictly_clean"),
    ("used", "proposal_approval_unused"),
])
def test_operator_check_reports_each_problem(fake_git, live_req, tmp_path, monkeypatch, problem, check,
                                            temp_proposal):
    from ira.operator_check import run_checks
    from ira.single_use import SingleUseLedger
    _fake_sys(monkeypatch, prefix=str(ROOT / ".venv") if problem == "prefix" else "/home/op/.venvs/ira-live",
              bytecode=problem != "bytecode", tty=problem != "tty")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    if problem == "env":
        monkeypatch.setenv("ANTHROPIC_API_KEY", KEY_SENTINEL)
    if problem == "dirty":
        fake_git["dirty"] = b"!! .venv/\n"
    if problem == "used":
        pid = temp_proposal["approval_id"]
        SingleUseLedger(tmp_path / "ledger").claim(pid, request_sha256="0" * 64, run_id="x")
    rows = run_checks(str(live_req), str(tmp_path / "ledger"))
    failed = [r["check"] for r in rows if not r["ok"]]
    assert check in failed
    assert all(KEY_SENTINEL not in r["detail"] for r in rows)
