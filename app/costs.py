"""Estimated running cost per client, from the usage counters the app already keeps (plans.usage_table).

It is an ESTIMATE for pricing decisions, not an invoice: the provider dashboards are the truth. The rates are
set in .env (see COST_* below) so you can match your real plans. Everything is in US dollars; set USD_TRY to also
show lira, and MONTHLY_FEE_TL to see each client's cost as a share of what they pay you.
"""
from __future__ import annotations

from . import config


def _rates() -> list[tuple[str, str, float, int]]:
    """(usage key, label, dollars per unit, unit size). Tokens are priced per million."""
    return [
        ("tokens_in", "AI reading (input tokens)", config.COST_IN_PER_M, 1_000_000),
        ("tokens_out", "AI writing (output tokens)", config.COST_OUT_PER_M, 1_000_000),
        ("voice", "Voice notes understood", config.COST_VOICE, 1),
        ("briefing", "Morning briefings (voice)", config.COST_BRIEFING, 1),
        ("flights", "Flight searches", config.COST_FLIGHT, 1),
    ]


def estimate(use: dict[str, int]) -> dict:
    rows, total = [], 0.0
    for key, label, rate, unit in _rates():
        qty = int(use.get(key, 0) or 0)
        usd = qty * rate / unit
        total += usd
        rows.append({"key": key, "label": label, "qty": qty, "usd": usd, "usd_txt": money(usd)})
    pct = None
    if config.USD_TRY > 0 and config.MONTHLY_FEE_TL > 0:
        pct = round(100 * total * config.USD_TRY / config.MONTHLY_FEE_TL)
    return {"rows": rows, "total": total, "total_txt": money(total), "tl_txt": lira(total), "pct": pct}


def money(usd: float) -> str:
    return f"${usd:,.2f}" if usd >= 0.01 or usd == 0 else "<$0.01"


def lira(usd: float) -> str:
    return f"{usd * config.USD_TRY:,.0f} TL" if config.USD_TRY > 0 else ""
