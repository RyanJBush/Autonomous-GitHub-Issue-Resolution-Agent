"""Replay provenance and the exact-hash review gate (offline). No approval record is created in the repository."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from conftest import PINNED, ROOT
from ira import runner
from ira.authorize import REVIEWED_LABEL
from ira.evaluate import make_request
from ira.prescreen import prescreen
from ira.status import Status
from ira.synthetic_exec import ExecutionReport
from test_live_adapter import message, run_live

CANARY_TEXT = (ROOT / "fixtures/responses/known_bad_execution_canary.json").read_text()


@pytest.fixture
def recorded(built_repo, tmp_path):
    pkt, _, _ = run_live(tmp_path, built_repo, message(), run_id="live-rec")
    rec = pkt["adapter"]["recording"]
    return pkt, Path(rec["path"]), rec["sha256"]


def replay_request(tmp_path, repo, rec_path, rec_sha, *, live_run_id="live-rec", run_id="replay-1", issue="clean"):
    req = make_request("unused", repo, run_id, issue_variant=issue)
    req["adapter"] = {"kind": "replay_recording", "recording_path": str(rec_path), "recording_sha256": rec_sha,
                      "live_run_id": live_run_id}
    path = tmp_path / f"{run_id}.json"
    path.write_text(json.dumps(req))
    return path


class CountingExecutor:
    def __init__(self, report=None):
        self.calls, self.report = 0, report

    def run(self, **kw):
        self.calls += 1
        if self.report is None:
            raise AssertionError("executor must not be reached")
        return self.report


def test_replay_preserves_provenance_and_label(built_repo, tmp_path, recorded):
    live_pkt, rec_path, rec_sha = recorded
    pkt, d = runner.execute(replay_request(tmp_path, built_repo, rec_path, rec_sha), tmp_path / "out2",
                            work_root=tmp_path / "w")
    assert pkt["run_kind"] == "replay_recording" and pkt["label"].startswith("REPLAY")
    assert "SYNTHETIC" not in pkt["label"]
    prov = pkt["adapter"]["replay_provenance"]
    assert prov["live_run_id"] == "live-rec" and prov["recording_sha256"] == rec_sha
    assert prov["live"]["request_id"] == "req_fake"
    assert pkt["response"]["raw_sha256"] == live_pkt["response"]["raw_sha256"]
    assert pkt["patch"]["sha256"] == live_pkt["patch"]["sha256"]
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("blocked_boundary_unverified",
                                                              "execution_boundary_unverified")


def test_unapproved_replay_never_reaches_executor(built_repo, tmp_path, recorded):
    _, rec_path, rec_sha = recorded
    ex = CountingExecutor()
    pkt, _ = runner.execute(replay_request(tmp_path, built_repo, rec_path, rec_sha), tmp_path / "out2",
                            work_root=tmp_path / "w", executor=ex)
    assert pkt["status_reason"] == "execution_not_authorized:replay_not_approved" and ex.calls == 0


def _replay_status(tmp_path, repo, rec_path, rec_sha, **kw):
    pkt, _ = runner.execute(replay_request(tmp_path, repo, rec_path, rec_sha, **kw), tmp_path / f"o-{len(list(tmp_path.iterdir()))}",
                            work_root=tmp_path / "w")
    return pkt["terminal_status"], pkt["status_reason"]


def test_tampered_recording_refused(built_repo, tmp_path, recorded):
    _, rec_path, rec_sha = recorded
    data = rec_path.read_bytes()
    bad = tmp_path / "bad.json"
    bad.write_bytes(data.replace(b"Collapse", b"Kollapse", 1))
    assert _replay_status(tmp_path, built_repo, bad, rec_sha) == ("adapter_error", "replay_recording_hash_mismatch")
    # Re-hashed file whose raw text no longer matches its recorded raw hash.
    assert _replay_status(tmp_path, built_repo, bad, hashlib.sha256(bad.read_bytes()).hexdigest()) == \
        ("adapter_error", "replay_raw_hash_mismatch")


def test_relabeled_recording_refused(built_repo, tmp_path, recorded):
    _, rec_path, _ = recorded
    rec = json.loads(rec_path.read_text())
    rec["label"] = "SYNTHETIC: hand-written fixture output; no model was called"
    bad = tmp_path / "relabel.json"
    bad.write_text(json.dumps(rec))
    assert _replay_status(tmp_path, built_repo, bad, hashlib.sha256(bad.read_bytes()).hexdigest()) == \
        ("adapter_error", "replay_recording_malformed")


def test_provenance_mismatches_refused(built_repo, tmp_path, recorded):
    _, rec_path, rec_sha = recorded
    assert _replay_status(tmp_path, built_repo, rec_path, rec_sha, live_run_id="other-run") == \
        ("adapter_error", "replay_live_run_id_mismatch")
    assert _replay_status(tmp_path, built_repo, rec_path, rec_sha, issue="injected") == \
        ("adapter_error", "replay_prompt_mismatch")


def test_repository_has_no_replay_approvals():
    data = json.loads((ROOT / PINNED["reviewed_model_outputs"]["path"]).read_text())
    assert data["label"] == REVIEWED_LABEL and data["entries"] == []


# --------------------------------------------------------------------------- exact-hash approval (temp files only)
def _approved_setup(tmp_path, live_pkt, rec_sha, **override):
    entry = {"recording_sha256": rec_sha, "live_run_id": live_pkt["run_id"],
             "response_sha256": live_pkt["response"]["raw_sha256"], "patch_sha256": live_pkt["patch"]["sha256"],
             "new_content_sha256": live_pkt["patch"]["new_content_sha256"], "base_commit": PINNED["base_commit"],
             "allowlisted_file": PINNED["allowlisted_file"], "reviewed_effect": "test-only entry",
             "operator_approval": {"approved": True, "approved_by": "test", "approved_at": "2026-11-15T00:00:00Z",
                                   "reviewed_whole_resulting_file": True}}
    for key, value in override.items():
        if key == "operator_approval":
            entry["operator_approval"] = {**entry["operator_approval"], **value}
        else:
            entry[key] = value
    f = tmp_path / "reviewed.json"
    f.write_text(json.dumps({"label": REVIEWED_LABEL, "entries": [entry]}))
    pinned = copy.deepcopy(PINNED)
    pinned["reviewed_model_outputs"] = {"path": str(f), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    return pinned


def _run_with(tmp_path, repo, rec_path, rec_sha, pinned, run_id):
    ex = CountingExecutor(ExecutionReport(Status.TESTS_PASSED_AWAITING_HUMAN_REVIEW, "all_expected_tests_passed"))
    live_run_id = json.loads(Path(rec_path).read_text())["live_run_id"]
    pkt, _ = runner.execute(replay_request(tmp_path, repo, rec_path, rec_sha, run_id=run_id, live_run_id=live_run_id),
                            tmp_path / run_id,
                            work_root=tmp_path / "w", executor=ex, pinned=pinned)
    return pkt, ex


def test_exact_approved_replay_reaches_executor(built_repo, tmp_path, recorded):
    live_pkt, rec_path, rec_sha = recorded
    pkt, ex = _run_with(tmp_path, built_repo, rec_path, rec_sha, _approved_setup(tmp_path, live_pkt, rec_sha), "ok")
    assert ex.calls == 1 and pkt["terminal_status"] == "tests_passed_awaiting_human_review"
    assert pkt["end_to_end_objective"]["status"] == "evaluated_awaiting_human_review"
    assert pkt["run_kind"] == "replay_recording"


@pytest.mark.parametrize("override,detail", [
    ({"patch_sha256": "0" * 64}, "replay_patch_sha256_mismatch"),
    ({"new_content_sha256": "0" * 64}, "replay_new_content_sha256_mismatch"),
    ({"response_sha256": "0" * 64}, "replay_response_sha256_mismatch"),
    ({"base_commit": "1" * 40}, "replay_base_commit_mismatch"),
    ({"live_run_id": "someone-else"}, "replay_live_run_id_mismatch"),
    ({"operator_approval": {"approved": False}}, "replay_operator_approval_missing"),
    ({"operator_approval": {"reviewed_whole_resulting_file": False}}, "replay_operator_approval_missing"),
    ({"operator_approval": {"approved_by": None}}, "replay_operator_approval_missing"),
])
def test_any_mismatch_blocks_replay(built_repo, tmp_path, recorded, override, detail):
    live_pkt, rec_path, rec_sha = recorded
    pkt, ex = _run_with(tmp_path, built_repo, rec_path, rec_sha, _approved_setup(tmp_path, live_pkt, rec_sha, **override),
                        "bad")
    assert pkt["status_reason"] == f"execution_not_authorized:{detail}" and ex.calls == 0


def test_prescreen_failure_blocks_even_an_approved_replay(built_repo, tmp_path):
    live_pkt, _, _ = run_live(tmp_path, built_repo, message(text=CANARY_TEXT), run_id="live-canary")
    rec = live_pkt["adapter"]["recording"]
    assert live_pkt["policy"]["prescreen"]["passed"] is False
    pkt, ex = _run_with(tmp_path, built_repo, Path(rec["path"]), rec["sha256"],
                        _approved_setup(tmp_path, live_pkt, rec["sha256"]), "canary")
    assert pkt["status_reason"] == "execution_not_authorized:replay_prescreen_failed" and ex.calls == 0


# --------------------------------------------------------------------------- limited pre-screen
GOOD = (ROOT / "demo/target/src/labels.py").read_text().replace("return text.lower()",
                                                                 'return " ".join(text.lower().split())')


@pytest.mark.parametrize("src,finding", [
    ("import os\n", "import_statement"),
    ("def f():\n    from os import path\n", "import_statement"),
    ("def f():\n    return open('x')\n", "forbidden_name:open"),
    ("def f():\n    return eval('1')\n", "forbidden_name:eval"),
    ("def f(x):\n    return x.__class__\n", "dunder_attribute:__class__"),
    ("X = 1\n", "module_level_statement:Assign"),
    ("print('hi')\n", "module_level_statement:Expr"),
    ("def f():\n    global y\n", "scope_statement:Global"),
    ("@decorate\ndef f():\n    pass\n", "decorator"),
    ("def f(:\n", "syntax_error:SyntaxError"),
])
def test_prescreen_flags(src, finding):
    result = prescreen(src.encode())
    assert result["passed"] is False and finding in result["findings"]


def test_prescreen_passes_plain_fix_and_states_its_limit():
    result = prescreen(GOOD.encode())
    assert result["passed"] and "not a security boundary" in result["label"]
