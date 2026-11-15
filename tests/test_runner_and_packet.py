import hashlib
import json

import pytest

from conftest import PINNED, ROOT
from ira import packet, runner
from ira.adapters import SYNTHETIC_LABEL, AdapterError, FixtureAdapter, load_cases
from ira.evaluate import run_eval
from ira.models import RequestError
from ira.status import M1_REACHABLE, Status

CASES = load_cases()


@pytest.fixture(scope="module")
def eval_out(tmp_path_factory):
    out = tmp_path_factory.mktemp("eval") / "run"
    return out, run_eval(out)


def test_all_fixture_cases_match(eval_out):
    _, summary = eval_out
    bad = [c for c in summary["cases"] if not c["match"]]
    assert not bad and summary["total"] == len(CASES) and summary["label"] == SYNTHETIC_LABEL


def test_every_m1_status_except_internal_ones_is_covered_by_fixtures():
    covered = {c["expected_status"] for c in CASES}
    assert covered == M1_REACHABLE - {Status.APPLY_FAILED, Status.EXECUTION_ERROR}


@pytest.mark.parametrize("case", [c["id"] for c in CASES])
def test_packet_complete_and_consistent(eval_out, case):
    out, _ = eval_out
    d = out / "packets" / case
    p = packet.verify(d)  # manifest hashes + null/reason invariant
    assert not packet.check_unavailable_invariant(p)
    assert p["run_kind"] == "synthetic_fixture" and p["label"].startswith("SYNTHETIC")
    assert p["terminal_status"] in {s.value for s in M1_REACHABLE}
    assert p["human_review"] == {"required": True, "pr_creation_authorized": False, "merge_authorized": False,
                                 "note": p["human_review"]["note"]}
    assert p["execution"]["candidate_executed"] is False and p["execution"]["boundary_verified"] is False
    assert p["tests"]["baseline"] is None and p["tests"]["candidate"] is None
    req_bytes = (out / "requests" / f"{case}.json").read_bytes()
    assert p["request"]["sha256"] == hashlib.sha256(req_bytes).hexdigest()
    assert p["adapter"]["kind"] == "synthetic_fixture" and p["adapter"]["model"] is None
    assert p["adapter"]["calls"] <= 1
    if p["response"]:
        # The adapter reads at most max_response_bytes + 1; the packet hashes exactly what was received.
        raw = (ROOT / "fixtures" / "responses" / f"{case}.json").read_bytes()[:32769]
        assert p["response"]["raw_sha256"] == hashlib.sha256(raw).hexdigest()
        assert p["response"]["stored_bytes"] <= 32768
    if p["patch"]:
        assert p["patch"]["sha256"] == hashlib.sha256(p["patch"]["diff"].encode()).hexdigest()
        assert p["apply"]["matches_policy_content"] is True
        assert p["terminal_status"] == "blocked_boundary_unverified"
    if p["base"]:
        assert p["base"]["base_commit"] == PINNED["base_commit"]
    assert p["prompt"] is None or p["prompt"]["template_version"] == "ira.prompt.v1"


def test_review_markdown_escapes_hostile_text(eval_out):
    out, _ = eval_out
    md = (out / "packets" / "schema_bidi_in_summary" / "review.md").read_text()
    assert "‮" not in md and "\\u202e" in md
    md2 = (out / "packets" / "policy_escape_sequence" / "review.md").read_text()
    assert "\x1b" not in md2


def test_oversized_response_is_bounded_in_packet(eval_out):
    out, _ = eval_out
    p = json.loads((out / "packets" / "schema_oversized" / "packet.json").read_text())
    assert p["response"]["raw_bytes"] == 32769 and p["response"]["stored_bytes"] == 32768
    assert p["response"]["truncated"] is True


def test_secrets_in_environment_never_reach_packet(built_repo, write_request, tmp_path, monkeypatch):
    secret = "sk-test-SHOULD-NEVER-APPEAR-0123456789"
    for name in ("ANTHROPIC_API_KEY", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY", "IRA_FAKE_SECRET"):
        monkeypatch.setenv(name, secret)
    pkt, d = runner.execute(write_request("good_trim_collapse", built_repo), tmp_path / "o",
                            work_root=tmp_path / "w")
    for f in ("packet.json", "review.md", "manifest.json"):
        assert secret not in (d / f).read_text()


def test_one_attempt_per_run_id(built_repo, write_request, tmp_path):
    req = write_request("good_trim_collapse", built_repo, run_id="once")
    runner.execute(req, tmp_path / "o", work_root=tmp_path / "w")
    with pytest.raises(RequestError):
        runner.execute(req, tmp_path / "o", work_root=tmp_path / "w")


def test_base_failure_stops_before_adapter(fresh_repo, write_request, tmp_path):
    (fresh_repo / "untracked.py").write_text("")
    calls = []

    def factory(spec):
        calls.append(spec)
        return FixtureAdapter(spec.fixture_case)
    pkt, _ = runner.execute(write_request("good_trim_collapse", fresh_repo), tmp_path / "o",
                            adapter_factory=factory, work_root=tmp_path / "w")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("rejected_policy", "dirty_tree")
    assert calls == [] and pkt["prompt"] is None and pkt["response"] is None
    assert pkt["stage_reached"] == "base_verification"


def test_trusted_test_identity_mismatch(built_repo, tmp_path):
    from ira.evaluate import make_request
    req = make_request("good_trim_collapse", built_repo, "tt")
    req["trusted_test"]["sha256"] = "0" * 64
    path = tmp_path / "r.json"
    path.write_text(json.dumps(req))
    pkt, _ = runner.execute(path, tmp_path / "o", work_root=tmp_path / "w")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("rejected_policy", "trusted_test_not_pinned")


def test_adapter_called_exactly_once(built_repo, write_request, tmp_path):
    holder = {}

    def factory(spec):
        holder["a"] = FixtureAdapter(spec.fixture_case)
        return holder["a"]
    runner.execute(write_request("good_trim_collapse", built_repo), tmp_path / "o", adapter_factory=factory,
                   work_root=tmp_path / "w")
    assert holder["a"].calls == 1
    with pytest.raises(AdapterError):
        holder["a"].propose("again", 100)


def test_internal_error_yields_execution_error_packet(built_repo, write_request, tmp_path, monkeypatch):
    monkeypatch.setattr(runner.policy, "validate", lambda *a, **k: 1 / 0)
    pkt, d = runner.execute(write_request("good_trim_collapse", built_repo), tmp_path / "o",
                            work_root=tmp_path / "w")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("execution_error", "internal_error:ZeroDivisionError")
    packet.verify(d)


def test_apply_failed_reachable_via_fault_injection(built_repo, write_request, tmp_path, monkeypatch):
    from ira.scratch import ApplyFailed

    def boom(*a, **k):
        raise ApplyFailed("git_apply_failed", "injected")
    monkeypatch.setattr(runner, "apply_and_confirm", boom)
    pkt, d = runner.execute(write_request("good_trim_collapse", built_repo), tmp_path / "o",
                            work_root=tmp_path / "w")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("apply_failed", "git_apply_failed")
    assert pkt["apply"] is None and "apply" in pkt["unavailable"]
    packet.verify(d)


def test_tampered_packet_fails_verification(built_repo, write_request, tmp_path):
    _, d = runner.execute(write_request("good_trim_collapse", built_repo), tmp_path / "o", work_root=tmp_path / "w")
    text = (d / "packet.json").read_text().replace("blocked_boundary_unverified", "tests_passed_awaiting_human_review")
    (d / "packet.json").write_text(text)
    with pytest.raises(ValueError):
        packet.verify(d)


def test_prompt_contains_only_allowed_inputs(built_repo, write_request, tmp_path, monkeypatch):
    seen = {}

    class Spy(FixtureAdapter):
        def propose(self, prompt, max_bytes):
            seen["prompt"] = prompt
            return super().propose(prompt, max_bytes)
    monkeypatch.setenv("IRA_ENV_SHOULD_NOT_LEAK", "leak-marker-xyz")
    runner.execute(write_request("good_trim_collapse", built_repo), tmp_path / "o",
                   adapter_factory=lambda s: Spy(s.fixture_case), work_root=tmp_path / "w")
    from ira import prompt as pr
    from ira.evaluate import load_issue
    title, body = load_issue("clean")
    lim = json.loads((tmp_path / "good_trim_collapse.request.json").read_text())["limits"]
    expected = pr.build_prompt(base_commit=PINNED["base_commit"], path=PINNED["allowlisted_file"], issue_title=title,
                               issue_body=body, source=(ROOT / "demo/target/src/labels.py").read_bytes(), limits=lim)
    assert seen["prompt"] == expected
    assert "leak-marker-xyz" not in seen["prompt"] and str(built_repo) not in seen["prompt"]
    assert "test_issue" not in seen["prompt"]  # trusted tests are hidden from the model


def test_injected_issue_is_data_only(built_repo, write_request, tmp_path):
    pkt_clean, _ = runner.execute(write_request("good_trim_collapse", built_repo, run_id="c"), tmp_path / "o",
                                  work_root=tmp_path / "w")
    pkt_inj, _ = runner.execute(write_request("good_trim_collapse", built_repo, run_id="i", issue_variant="injected"),
                                tmp_path / "o", work_root=tmp_path / "w")
    strip = lambda p: [(d["check"], d["result"], d["code"]) for d in p["policy"]["decisions"]]
    assert strip(pkt_clean) == strip(pkt_inj)
    assert pkt_clean["policy"]["hard_caps"] == pkt_inj["policy"]["hard_caps"]
    assert pkt_clean["terminal_status"] == pkt_inj["terminal_status"]
