"""One bounded Anthropic Messages API request (Milestone 3). Never executes the response.

Request: one `messages.create` call, `max_retries=0`, no tools, no system prompt, no fallbacks, explicit effort.
Thinking is left at the model default (adaptive). Verified against anthropic==2.8.0 (`messages.create` accepts
`model`, `max_tokens`, `messages`, `output_config`) and the published docs fetched 2026-11-15.
"""
from __future__ import annotations

import hashlib

from . import pricing
from .adapters import AdapterError

ADAPTER_VERSION = "ira.live_adapter.v1"
LIVE_LABEL = "LIVE MODEL RUN: real model output; never executed"


def _sdk_version() -> str | None:
    try:
        import anthropic
        return anthropic.__version__
    except ImportError:
        return None


def default_client_factory(spec):
    import anthropic  # imported only when a live call is actually being made
    return anthropic.Anthropic(max_retries=0, timeout=float(spec.timeout_s))


def classify_api_error(error: Exception) -> str:
    """Distinct, honest reasons. Order matters: APITimeoutError subclasses APIConnectionError."""
    try:
        import anthropic
    except ImportError:
        return f"adapter_unexpected:{type(error).__name__}"
    status = getattr(error, "status_code", None)
    message = str(getattr(error, "message", "") or "")
    body = getattr(error, "body", None)
    code = ((body or {}).get("error") or {}).get("details", {}).get("error_code") if isinstance(body, dict) else None
    if isinstance(error, anthropic.APITimeoutError):
        return "api_timeout"
    if isinstance(error, anthropic.APIConnectionError):
        return "api_connection_error"
    if isinstance(error, anthropic.AuthenticationError):
        return "api_authentication_failed"
    if isinstance(error, anthropic.PermissionDeniedError):
        return "api_permission_denied"
    if isinstance(error, anthropic.NotFoundError):
        return "api_not_found"
    if isinstance(error, anthropic.RateLimitError):
        return "api_spend_cap_reached" if code == "enforced_spend_limit_reached" else (
            "api_ramp_limited" if code == "ramp_limit_exceeded" else "api_rate_limited")
    if isinstance(error, anthropic.BadRequestError):
        return "api_spend_limit_reached" if "specified" in message and "usage limits" in message else "api_bad_request"
    if isinstance(error, anthropic.APIStatusError):
        return f"api_server_error_{status}" if status and status >= 500 else f"api_status_{status}"
    return f"adapter_unexpected:{type(error).__name__}"


def _usage_dict(usage) -> dict:
    if usage is None:
        return {}
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
            "service_tier", "speed", "inference_geo")
    return {k: getattr(usage, k, None) for k in keys if getattr(usage, k, None) is not None}


class LiveAdapter:
    def __init__(self, spec, client_factory=default_client_factory):
        self.spec, self.client_factory, self.calls = spec, client_factory, 0
        self.record = {"adapter_version": ADAPTER_VERSION, "sdk": "anthropic", "sdk_version": _sdk_version(),
                       "requested_model": spec.model, "authorized_response_models": list(spec.authorized_response_models),
                       "max_tokens": spec.max_tokens, "effort": spec.effort, "timeout_s": spec.timeout_s,
                       "max_retries": 0, "tools": None, "fallbacks": None, "system_prompt": None,
                       "cost_cap_usd": f"{spec.cost_cap_cents / 100:.2f}", "request_sent": False}

    def identity(self) -> dict:
        return {"kind": "live_model", "name": "AnthropicMessagesAdapter", "label": LIVE_LABEL,
                "model": self.spec.model, "adapter_version": ADAPTER_VERSION, "sdk_version": self.record["sdk_version"],
                "fixture_case": None, "fixture_sha256": None}

    def _fail(self, code, detail=""):
        self.record["adapter_outcome"] = code
        raise AdapterError(code, detail)

    def propose(self, prompt: str, max_bytes: int) -> bytes:
        self.calls += 1
        if self.calls > 1:
            raise AdapterError("attempt_limit", "only one proposal per run is allowed")
        prompt_bytes = len(prompt.encode("utf-8"))
        est = pricing.estimate_call(self.spec.model, prompt_bytes, self.spec.max_tokens)
        self.record["pre_call_estimate"] = est
        if not pricing.within_cap(est, self.spec.cost_cap_cents):
            self._fail("cost_estimate_exceeds_cap", "pre-call estimate exceeds the request cost cap; no request sent")
        client = self.client_factory(self.spec)
        try:
            self.record["request_sent"] = True
            response = client.messages.create(model=self.spec.model, max_tokens=self.spec.max_tokens,
                                              output_config={"effort": self.spec.effort},
                                              messages=[{"role": "user", "content": prompt}])
        except Exception as error:  # every failure is reported; nothing is retried
            self.record["error"] = {"class": type(error).__name__, "status_code": getattr(error, "status_code", None),
                                    "message": str(getattr(error, "message", error))[:200]}
            self._fail(classify_api_error(error), "provider call failed")
        usage = _usage_dict(getattr(response, "usage", None))
        stop = getattr(response, "stop_reason", None)
        details = getattr(response, "stop_details", None)
        content = list(getattr(response, "content", None) or [])
        kinds = [getattr(b, "type", None) for b in content]
        self.record.update({
            "response_model": getattr(response, "model", None), "message_id": getattr(response, "id", None),
            "request_id": getattr(response, "_request_id", None), "stop_reason": stop,
            "stop_details_category": getattr(details, "category", None) if details else None,
            "usage": usage, "content_block_types": kinds,
        })
        if self.spec.model in pricing.PRICES and usage:
            self.record["cost_from_usage"] = pricing.cost_from_usage(self.spec.model, usage)
        if self.record["response_model"] not in self.spec.authorized_response_models:
            self._fail(f"unexpected_model_identity:{str(self.record['response_model'])[:64]}")
        if stop == "refusal":
            self._fail(f"model_refusal:{self.record['stop_details_category'] or 'uncategorized'}")
        if stop == "max_tokens":
            self._fail("output_truncated_max_tokens")
        if stop == "model_context_window_exceeded":
            self._fail("output_truncated_context_window")
        if stop != "end_turn":
            self._fail(f"unexpected_stop_reason:{str(stop)[:40]}")
        others = sorted({k for k in kinds if k not in ("text", "thinking")})
        if others:
            self._fail(f"unexpected_content_block:{','.join(str(o) for o in others)[:80]}")
        texts = [b for b in content if getattr(b, "type", None) == "text"]
        if len(texts) != 1:
            self._fail(f"text_block_count_{len(texts)}")
        text = getattr(texts[0], "text", None)
        if not isinstance(text, str) or not text:
            self._fail("empty_text")
        raw = text.encode("utf-8")
        self.record.update({"adapter_outcome": "response_received", "raw_sha256": hashlib.sha256(raw).hexdigest(),
                            "raw_bytes": len(raw)})
        return raw
