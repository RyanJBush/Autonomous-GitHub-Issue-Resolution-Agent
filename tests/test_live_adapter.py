"""Live adapter, fully offline: a fake client stands in for the provider; no network request is ever made."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from conftest import ROOT
from ira import runner
from ira.evaluate import make_request
from ira.live_adapter import LiveAdapter, default_client_factory
from ira.models import RequestError, load_request
from ira.single_use import SingleUseLedger

GOOD_TEXT = (ROOT / "fixtures/responses/good_trim_collapse.json").read_text()
LIVE = {"kind": "live_model", "model": "claude-sonnet-5-5", "max_tokens": 8000, "effort": "medium", "timeout_s": 120,
        "cost_cap_cents": 25, "authorized_response_models": ["claude-sonnet-5-5"]}
REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def message(text=GOOD_TEXT, *, stop="end_turn", model="claude-sonnet-5-5", blocks=None, category=None):
    return SimpleNamespace(
        id="msg_fake", model=model, stop_reason=stop, _request_id="req_fake",
        stop_details=SimpleNamespace(category=category) if stop == "refusal" else None,
        content=blocks if blocks is not None else [SimpleNamespace(type="thinking", thinking=""),
                                                   SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=700, output_tokens=900, cache_read_input_tokens=0,
                              cache_creation_input_tokens=0, service_tier="standard"))


class FakeClient:
    def __init__(self, result):
        self.result, self.calls = result, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def live_request(tmp_path, repo, run_id="live-test", **adapter):
    req = make_request("unused", repo, run_id)
    req["adapter"] = {**LIVE, **adapter}
    path = tmp_path / f"{run_id}.json"
    path.write_text(json.dumps(req))
    return path


def approval_for(path, approval_id=None):
    return {"operator_approval": {"approved": True, "approved_by": "test", "approved_at": "2026-11-15T00:00:00Z"},
            "approval_id": approval_id or hashlib.sha256(str(path).encode()).hexdigest()[:32],
            "binds": {"request_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}}


def run_live(tmp_path, repo, result, *, run_id="live-test", executor=None, ledger=None, approval_id=None, **adapter):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = live_request(tmp_path, repo, run_id, **adapter)
    ledger = ledger or SingleUseLedger(tmp_path / "ledger")
    client = FakeClient(result)
    made = []

    def factory(spec):
        a = LiveAdapter(spec, client_factory=lambda s: (made.append(s), client)[1])
        return a
    pkt, d = runner.execute(path, tmp_path / "out", adapter_factory=factory, work_root=tmp_path / "w",
                            executor=executor, live_approval=approval_for(path, approval_id), single_use=ledger)
    return pkt, d, client


class RecordingExecutor:
    calls = 0

    def run(self, **kw):
        RecordingExecutor.calls += 1
        raise AssertionError("executor must not be reached")


def test_success_records_everything_and_is_never_executed(built_repo, tmp_path):
    pkt, d, client = run_live(tmp_path, built_repo, message())
    assert len(client.calls) == 1
    call = client.calls[0]
    assert set(call) == {"model", "max_tokens", "output_config", "messages"}  # no tools, system, fallbacks, thinking
    assert call["model"] == "claude-sonnet-5-5" and call["max_tokens"] == 8000
    assert call["output_config"] == {"effort": "medium"} and len(call["messages"]) == 1
    assert pkt["run_kind"] == "live_model" and pkt["label"] == "LIVE MODEL RUN"
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("blocked_boundary_unverified",
                                                              "execution_boundary_unverified")
    live = pkt["adapter"]["live"]
    assert live["requested_model"] == live["response_model"] == "claude-sonnet-5-5"
    assert live["request_id"] == "req_fake" and live["usage"]["output_tokens"] == 900
    assert live["pre_call_estimate"]["label"].startswith("ESTIMATE") and "cost_from_usage" in live
    rec = pkt["adapter"]["recording"]
    assert rec["written"] and hashlib.sha256(Path(rec["path"]).read_bytes()).hexdigest() == rec["sha256"]
    assert pkt["end_to_end_objective"]["status"] == "not_met"
    assert pkt["execution"]["candidate_executed"] is False
    assert pkt["patch"]["new_content"].count("def normalize_label") == 1
    assert "Whole resulting file" in (d / "review.md").read_text()


def test_live_response_never_reaches_executor(built_repo, tmp_path):
    RecordingExecutor.calls = 0
    pkt, _, _ = run_live(tmp_path, built_repo, message(), executor=RecordingExecutor())
    assert pkt["status_reason"] == "execution_not_authorized:untrusted_execution_not_enabled"
    assert RecordingExecutor.calls == 0


@pytest.mark.parametrize("result,reason", [
    (message(stop="refusal", category="cyber"), "model_refusal:cyber"),
    (message(stop="refusal"), "model_refusal:uncategorized"),
    (message(stop="max_tokens"), "output_truncated_max_tokens"),
    (message(stop="model_context_window_exceeded"), "output_truncated_context_window"),
    (message(stop="pause_turn"), "unexpected_stop_reason:pause_turn"),
    (message(model="claude-opus-5-5"), "unexpected_model_identity:claude-opus-5-5"),
    (message(blocks=[SimpleNamespace(type="text", text="a"), SimpleNamespace(type="text", text="b")]),
     "text_block_count_2"),
    (message(blocks=[SimpleNamespace(type="thinking", thinking="")]), "text_block_count_0"),
    (message(blocks=[SimpleNamespace(type="tool_use", id="t", name="x", input={})]), "unexpected_content_block:tool_use"),
    (message(text=""), "empty_text"),
])
def test_response_failures_are_distinct_adapter_errors(built_repo, tmp_path, result, reason):
    pkt, _, client = run_live(tmp_path, built_repo, result)
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("adapter_error", reason)
    assert len(client.calls) == 1 and pkt["adapter"]["recording"] is None
    assert pkt["adapter"]["live"]["adapter_outcome"] == reason
    assert pkt["end_to_end_objective"]["status"] == "not_met"


def _status(cls, code, body=None, msg="error"):
    return cls(msg, response=httpx2.Response(code, request=REQ), body=body)


@pytest.mark.parametrize("error,reason", [
    (_status(anthropic.AuthenticationError, 401), "api_authentication_failed"),
    (_status(anthropic.PermissionDeniedError, 403), "api_permission_denied"),
    (_status(anthropic.NotFoundError, 404), "api_not_found"),
    (_status(anthropic.RateLimitError, 429), "api_rate_limited"),
    (_status(anthropic.RateLimitError, 429, {"error": {"details": {"error_code": "enforced_spend_limit_reached"}}}),
     "api_spend_cap_reached"),
    (_status(anthropic.RateLimitError, 429, {"error": {"details": {"error_code": "ramp_limit_exceeded"}}}),
     "api_ramp_limited"),
    (_status(anthropic.BadRequestError, 400, msg="You have reached your specified API usage limits"),
     "api_spend_limit_reached"),
    (_status(anthropic.BadRequestError, 400), "api_bad_request"),
    (_status(anthropic.InternalServerError, 500), "api_server_error_500"),
    (_status(anthropic.APIStatusError, 529), "api_server_error_529"),
    (anthropic.APITimeoutError(request=REQ), "api_timeout"),
    (anthropic.APIConnectionError(request=REQ, message="down"), "api_connection_error"),
    (RuntimeError("boom"), "adapter_unexpected:RuntimeError"),
])
def test_api_errors_have_distinct_reasons_and_no_retry(built_repo, tmp_path, error, reason):
    pkt, _, client = run_live(tmp_path, built_repo, error)
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("adapter_error", reason)
    assert len(client.calls) == 1  # exactly one attempt
    assert pkt["adapter"]["live"]["request_sent"] is True


def test_oversized_and_malformed_outputs_are_honest_results(built_repo, tmp_path):
    pkt, _, _ = run_live(tmp_path, built_repo, message(text="x" * 40_000), run_id="big")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("rejected_schema", "response_too_large")
    assert pkt["adapter"]["recording"]["written"] and pkt["end_to_end_objective"]["status"] == "not_met"
    pkt, _, _ = run_live(tmp_path, built_repo, message(text="Here is a fix: ..."), run_id="prose")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("rejected_schema", "invalid_json")


def test_cost_cap_refuses_before_any_request(built_repo, tmp_path):
    pkt, _, client = run_live(tmp_path, built_repo, message(), cost_cap_cents=1)
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("adapter_error", "cost_estimate_exceeds_cap")
    assert client.calls == [] and pkt["adapter"]["live"]["request_sent"] is False


@pytest.mark.parametrize("approval", [None, {"operator_approval": {"approved": False}, "binds": {}},
                                      "wrong-digest"])
def test_live_call_refused_without_verified_approval(built_repo, tmp_path, approval):
    path = live_request(tmp_path, built_repo)
    if approval == "wrong-digest":
        approval = {"operator_approval": {"approved": True}, "binds": {"request_sha256": "0" * 64}}
    built = []
    with pytest.raises(RequestError):
        runner.execute(path, tmp_path / "out", work_root=tmp_path / "w", live_approval=approval,
                       adapter_factory=lambda s: built.append(s), single_use=SingleUseLedger(tmp_path / "l"))
    assert built == [] and not (tmp_path / "out" / "live-test").exists()


def test_api_key_never_recorded(built_repo, tmp_path, monkeypatch):
    secret = "sk-ant-api03-SHOULD-NEVER-APPEAR-abcdef"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    pkt, d, _ = run_live(tmp_path, built_repo, message())
    for f in [d / "packet.json", d / "review.md", Path(pkt["adapter"]["recording"]["path"])]:
        assert secret not in f.read_text()


def test_one_request_per_adapter_and_client_configuration(monkeypatch):
    spec = load_request(json.dumps({**json.loads((ROOT / "demo/live/request.json").read_text())}).encode()).adapter
    a = LiveAdapter(spec, client_factory=lambda s: FakeClient(message()))
    with pytest.raises(Exception) as info:
        a.propose("p", 32768)  # no single-use claim configured: refuses before sending
    assert info.value.code == "single_use_claim_missing"
    a = LiveAdapter(spec, client_factory=lambda s: FakeClient(message()))
    a.before_send = lambda: "marker"
    a.propose("p", 32768)
    with pytest.raises(Exception):
        a.propose("p", 32768)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy-offline")
    client = default_client_factory(spec)  # constructing a client makes no request
    assert client.max_retries == 0 and float(client.timeout) == 120.0


def test_request_schema_limits(built_repo, tmp_path):
    base = json.loads(live_request(tmp_path, built_repo).read_text())
    for bad in ({"model": "claude-opus-5-5"}, {"max_tokens": 9000}, {"cost_cap_cents": 26}, {"effort": "max"},
                {"authorized_response_models": ["claude-opus-5-5"]}, {"timeout_s": 301}, {"tools": []}):
        req = {**base, "adapter": {**base["adapter"], **bad}}
        with pytest.raises(RequestError):
            load_request(json.dumps(req).encode())


def test_real_sdk_client_cannot_reach_network_in_tests(built_repo, tmp_path, monkeypatch):
    """With the real client factory and a dummy key, the request fails offline as api_connection_error."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy-offline")
    path = live_request(tmp_path, built_repo, run_id="real-client", timeout_s=10)
    pkt, _ = runner.execute(path, tmp_path / "out", work_root=tmp_path / "w", live_approval=approval_for(path),
                            single_use=SingleUseLedger(tmp_path / "ledger"))
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("adapter_error", "api_connection_error")



# --------------------------------------------------------------------------- finding 1: no provider text in artifacts
SENTINEL = "FAKE_SECRET_SENTINEL_123"


@pytest.mark.parametrize("error", [
    _status(anthropic.AuthenticationError, 401, {"error": {"type": "authentication_error", "message": SENTINEL}},
            msg=f"invalid x-api-key {SENTINEL}"),
    _status(anthropic.BadRequestError, 400, {"error": {"type": SENTINEL, "details": {"error_code": SENTINEL}}},
            msg=f"bad request echoing {SENTINEL}"),
    _status(anthropic.InternalServerError, 500, SENTINEL, msg=SENTINEL),
    anthropic.APIConnectionError(request=REQ, message=f"proxy said {SENTINEL}"),
    RuntimeError(f"client blew up with {SENTINEL}"),
])
def test_exception_text_never_reaches_durable_artifacts(built_repo, tmp_path, error):
    pkt, d, _ = run_live(tmp_path, built_repo, error)
    assert pkt["terminal_status"] == "adapter_error"
    rec = pkt["adapter"]["live"]["error"]
    assert set(rec) == {"class", "status_code", "provider_error_type", "provider_error_code", "note"}
    for f in [p for p in (tmp_path / "out").rglob("*") if p.is_file()]:
        assert SENTINEL not in f.read_text(errors="replace"), f
    assert SENTINEL not in json.dumps(pkt["trace"]) and SENTINEL not in json.dumps(pkt)


def test_safe_provider_codes_are_kept(built_repo, tmp_path):
    err = _status(anthropic.RateLimitError, 429, {"error": {"type": "rate_limit_error",
                                                            "details": {"error_code": "enforced_spend_limit_reached"}}})
    pkt, _, _ = run_live(tmp_path, built_repo, err)
    rec = pkt["adapter"]["live"]["error"]
    assert (rec["status_code"], rec["provider_error_type"], rec["provider_error_code"]) == (
        429, "rate_limit_error", "enforced_spend_limit_reached")


# --------------------------------------------------------------------------- finding 2: local single use
def test_approval_cannot_be_reused_on_this_machine(built_repo, tmp_path):
    ledger = SingleUseLedger(tmp_path / "ledger")
    pkt, _, c1 = run_live(tmp_path / "a", built_repo, message(), ledger=ledger, approval_id="1" * 32)
    assert pkt["adapter"]["live"]["single_use_marker"] == "1" * 32 + ".spent" and len(c1.calls) == 1
    pkt, _, c2 = run_live(tmp_path / "b", built_repo, message(), ledger=ledger, approval_id="1" * 32,
                          run_id="live-again")
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("adapter_error", "approval_already_used")
    assert c2.calls == [] and pkt["adapter"]["live"]["request_sent"] is False


def test_timeout_consumes_the_approval(built_repo, tmp_path):
    ledger = SingleUseLedger(tmp_path / "ledger")
    pkt, _, _ = run_live(tmp_path / "a", built_repo, anthropic.APITimeoutError(request=REQ), ledger=ledger,
                         approval_id="2" * 32)
    assert pkt["status_reason"] == "api_timeout" and ledger.is_used("2" * 32)
    pkt, _, c = run_live(tmp_path / "b", built_repo, message(), ledger=ledger, approval_id="2" * 32, run_id="retry")
    assert pkt["status_reason"] == "approval_already_used" and c.calls == []


def test_refusals_before_sending_do_not_consume(built_repo, tmp_path):
    ledger = SingleUseLedger(tmp_path / "ledger")
    pkt, _, c = run_live(tmp_path / "a", built_repo, message(), ledger=ledger, approval_id="3" * 32, cost_cap_cents=1)
    assert pkt["status_reason"] == "cost_estimate_exceeds_cap" and not ledger.is_used("3" * 32) and c.calls == []


def _claim(args):
    directory, approval_id = args
    from ira.single_use import AlreadyUsed, SingleUseLedger as L
    try:
        L(directory).claim(approval_id, request_sha256="0" * 64, run_id="r")
        return "claimed"
    except AlreadyUsed:
        return "refused"


def test_concurrent_claims_admit_exactly_one(tmp_path):
    import multiprocessing
    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(8) as pool:
        results = pool.map(_claim, [(str(tmp_path / "ledger"), "4" * 32)] * 16)
    assert results.count("claimed") == 1 and results.count("refused") == 15


def test_ledger_must_be_outside_checkout_and_live_needs_one(built_repo, tmp_path):
    from ira.single_use import LedgerError
    with pytest.raises(LedgerError):
        SingleUseLedger(ROOT / "out" / "ledger")
    path = live_request(tmp_path, built_repo)
    with pytest.raises(RequestError):
        runner.execute(path, tmp_path / "out", work_root=tmp_path / "w", live_approval=approval_for(path))



def test_client_setup_failure_does_not_consume_and_is_mapped(built_repo, tmp_path):
    ledger = SingleUseLedger(tmp_path / "ledger")
    path = live_request(tmp_path, built_repo, run_id="nokey")

    def factory(spec):
        a = LiveAdapter(spec, client_factory=lambda s: (_ for _ in ()).throw(
            anthropic.AnthropicError(f"missing key {SENTINEL}")))
        return a
    pkt, d = runner.execute(path, tmp_path / "out", adapter_factory=factory, work_root=tmp_path / "w",
                            live_approval=approval_for(path, "5" * 32), single_use=ledger)
    assert (pkt["terminal_status"], pkt["status_reason"]) == ("adapter_error", "client_setup_failed:AnthropicError")
    assert not ledger.is_used("5" * 32) and pkt["adapter"]["live"]["request_sent"] is False
    assert SENTINEL not in json.dumps(pkt)
