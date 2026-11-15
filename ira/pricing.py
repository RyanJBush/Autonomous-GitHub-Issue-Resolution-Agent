"""Published model prices and cost ESTIMATES (Codex M3 amendment 1).

Nothing here is a guaranteed bound. The output side uses `max_tokens`, which the provider documents as a strict limit
that includes thinking tokens, billed as output. The input side is a heuristic token estimate (no provider token count
is used here, because the provider's token-counting endpoint needs credentials). The provider's invoice is authoritative.
"""
from __future__ import annotations

from decimal import ROUND_UP, Decimal

PRICES = {
    "claude-sonnet-5-5": {
        "input_usd_per_mtok": Decimal("2"),
        "output_usd_per_mtok": Decimal("10"),
        "cache_read_usd_per_mtok": Decimal("0.10"),
        "cache_write_5m_usd_per_mtok": Decimal("2.50"),
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
        "retrieved": "2026-11-15",
    },
}
SOURCES = {
    "output_includes_thinking": "https://platform.claude.com/docs/en/build-with-claude/thinking "
                                "(thinking tokens are billed as output tokens and count toward max_tokens; "
                                "max_tokens is enforced as a strict limit)",
    "token_counting": "https://platform.claude.com/docs/en/build-with-claude/token-counting "
                      "(free, rate-limited, requires an API key; returns an estimate)",
    "spend_limits": "https://platform.claude.com/docs/en/api/rate-limits#setting-your-own-spend-limit",
}
FRAMING_TOKENS_ESTIMATE = 64  # message framing; an assumption, not a published number
_MTOK = Decimal(1_000_000)


def _usd(x: Decimal) -> str:
    return str(x.quantize(Decimal("0.000001"), rounding=ROUND_UP))


def estimate_call(model: str, prompt_bytes: int, max_tokens: int) -> dict:
    """Pre-call estimate. Input tokens are estimated as prompt bytes + framing (heuristic; for UTF-8 text a token is
    usually more than one byte, so this tends to overestimate, but it is not a guarantee)."""
    p = PRICES[model]
    input_tokens = prompt_bytes + FRAMING_TOKENS_ESTIMATE
    input_usd = Decimal(input_tokens) * p["input_usd_per_mtok"] / _MTOK
    output_usd = Decimal(max_tokens) * p["output_usd_per_mtok"] / _MTOK
    return {
        "label": "ESTIMATE (not a guaranteed bound; the provider invoice is authoritative)",
        "model": model,
        "input_tokens_estimate": input_tokens,
        "input_tokens_basis": "prompt UTF-8 bytes + 64 framing tokens (heuristic, not a provider token count)",
        "output_tokens_ceiling": max_tokens,
        "output_tokens_basis": "max_tokens, documented as a strict limit that includes thinking tokens",
        "input_usd_estimate": _usd(input_usd),
        "output_usd_at_ceiling": _usd(output_usd),
        "total_usd_estimate": _usd(input_usd + output_usd),
        "prices": {k: str(v) for k, v in p.items() if k.endswith("_mtok")},
        "price_source": p["source"], "price_retrieved": p["retrieved"],
        "assumptions": ["standard speed", "no Batch API", "no prompt caching expected", "no server tools"],
    }


def cost_from_usage(model: str, usage: dict) -> dict:
    """Post-call estimate from the provider-reported usage and the published base rates."""
    p = PRICES[model]
    inp = Decimal(int(usage.get("input_tokens") or 0)) * p["input_usd_per_mtok"]
    out = Decimal(int(usage.get("output_tokens") or 0)) * p["output_usd_per_mtok"]
    cr = Decimal(int(usage.get("cache_read_input_tokens") or 0)) * p["cache_read_usd_per_mtok"]
    cw = Decimal(int(usage.get("cache_creation_input_tokens") or 0)) * p["cache_write_5m_usd_per_mtok"]
    return {"label": "ESTIMATE from reported usage and published base rates; the provider invoice is authoritative",
            "total_usd_estimate": _usd((inp + out + cr + cw) / _MTOK), "price_source": p["source"],
            "price_retrieved": p["retrieved"]}


def within_cap(estimate: dict, cap_cents: int) -> bool:
    return Decimal(estimate["total_usd_estimate"]) <= Decimal(cap_cents) / Decimal(100)
