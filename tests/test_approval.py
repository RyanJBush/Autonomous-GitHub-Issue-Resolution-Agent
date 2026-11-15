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
    assert b["estimate"]["label"].startswith("ESTIMATE") and b["cost_cap_usd"] == "0.25"
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
                                         ("cost_cap_usd", "9.99")])
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
    req["adapter"]["cost_cap_cents"] = 1
    live_req.write_text(json.dumps(req))
    with pytest.raises(approval.ApprovalError) as info:
        approval.prepare(live_req, tmp_path / "c.json")
    assert info.value.code == "estimate_exceeds_request_cap"


@pytest.fixture
def no_adapter(monkeypatch):
    built = []

    class Boom:
        def __init__(self, *a, **k):
            built.append(1)
            raise AssertionError("adapter must not be constructed")
    monkeypatch.setattr(live_adapter, "LiveAdapter", Boom)
    return built


def test_cli_refuses_before_any_adapter(fake_git, live_req, tmp_path, no_adapter, capsys):
    out = tmp_path / "approval.json"
    approval.prepare(live_req, out)
    runs = tmp_path / "runs"
    assert cli.main(["run", "--request", str(live_req), "--out", str(runs)]) == 2           # live request, no --live
    assert cli.main(["run", "--live", "--request", str(live_req), "--out", str(runs)]) == 2  # no packet
    assert cli.main(["run", "--live", "--request", str(live_req), "--out", str(runs),
                     "--approval-packet", str(out)]) == 2                                    # not approved
    synthetic = ROOT / "demo/request.example.json"
    assert cli.main(["run", "--live", "--request", str(synthetic), "--out", str(runs)]) == 2  # --live on synthetic
    assert no_adapter == [] and not runs.exists()
    assert "operator_approval_missing" in capsys.readouterr().err


def test_committed_first_call_request_is_valid_and_bounded():
    from ira.models import load_request
    req = load_request((ROOT / "demo/live/request.json").read_bytes())
    a = req.adapter
    assert (a.kind, a.model, a.max_tokens, a.effort, a.cost_cap_cents) == ("live_model", "claude-sonnet-5-5", 8000,
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


def test_cli_refuses_out_inside_checkout_and_used_approvals(fake_git, live_req, tmp_path, no_adapter, capsys):
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
                     "--approval-packet", str(out), "--spent-ledger", str(tmp_path / "l")]) == 2
    assert "approval_already_used" in capsys.readouterr().err
    assert no_adapter == [] and not (tmp_path / "runs").exists()


def test_no_live_workflow_and_committed_proposal_is_unapproved():
    assert not (ROOT / ".github/workflows/live-proposal.yml").exists()
    proposal = ROOT / approval.PROPOSAL_PATH
    if proposal.exists():
        pkt = json.loads(proposal.read_text())
        assert pkt["operator_approval"]["approved"] is False and pkt["status"] == "awaiting_operator_approval"
        assert len(pkt["approval_id"]) == 32 and pkt["binds"]["procedure_path"] == approval.PROCEDURE_PATH
