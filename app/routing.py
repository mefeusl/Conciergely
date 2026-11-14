"""Model routing: the fast model (Haiku) answers almost everything; the main model is kept for heavy work.

Main model: charts, scans (including 30-day ones), backtests, scenarios, risk maths, long or analytical requests and
PDF documents. If the fast model starts a heavy tool in the middle of a reply, `escalate` moves the rest of that
reply to the main model. Photos stay on the fast model (it reads images well).
"""
from __future__ import annotations

import re

from . import config, textutil

LONG = 400  # characters: longer requests are usually multi-part
HEAVY_KEYWORDS = (
    "grafik", "chart", "tara", "tarama", "scan", "30 gun", "30 day", "30-day", "30gun", "backtest", "geri test",
    "senaryo", "scenario", "risk", "analiz", "analy", "karsilastir", "compare", "strateji", "strategy",
    "rapor", "report", "plan yap", "planla", "detayli", "in depth", "detailed", "pdf", "belge", "document",
    "pattern", "formasyon",
)
_HEAVY = re.compile(r"(?:^|\b)(?:" + "|".join(re.escape(k) for k in HEAVY_KEYWORDS) + ")")
# Tools that run big computations; once the fast model calls one, the main model finishes the reply.
HEAVY_TOOLS = frozenset({"use_playbook", "scan_results", "backtest_setup", "position_risk", "scenario",
                         "watchlist_scan", "price_levels", "market_pulse"})


def _has_document(attachments: list[dict] | None) -> bool:
    return any(isinstance(a, dict) and a.get("type") == "document" for a in attachments or [])


def choose(last_assistant: str, text: str, has_attachments: bool, extra: str | None,
           attachments: list[dict] | None = None) -> str:
    """Model id for this message."""
    smart, fast = config.CLAUDE_MODEL, config.MODEL_FAST
    if not config.MODEL_ROUTING or not fast or fast == smart or _disabled:
        return smart
    if _has_document(attachments):
        return smart
    text = (text or "").strip()
    if len(text) > LONG or _HEAVY.search(textutil.fold(text)):
        return smart
    return fast


def escalate(model: str, tool_names) -> str:
    """The model to use for the rest of a reply once these tools were called."""
    if model != config.CLAUDE_MODEL and any(n in HEAVY_TOOLS for n in tool_names):
        return config.CLAUDE_MODEL
    return model


_disabled = False  # set when the API says the cheap model does not exist for this account


def disable() -> None:
    global _disabled
    _disabled = True


def reset() -> None:
    global _disabled
    _disabled = False
