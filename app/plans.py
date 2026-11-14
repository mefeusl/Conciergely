"""Plans (basic / plus), feature gates and monthly usage counters.

Why: some features cost real money per use (flight searches, voice, long analyses). Plus unlocks them. Usage is
counted per client and month (shown in /admin); caps are optional (PLAN_LIMITS_JSON) and abuse.py freezes exploitation.

  * features in config.PLUS_ONLY are Plus-only: "flights", "trading", "briefing";
  * counters live in the usage table, one row per client, month and key;
  * limits come from LIMITS below, optionally overridden by PLAN_LIMITS_JSON.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config, db

log = logging.getLogger(__name__)

PLANS = ("lite", "basic", "plus")  # cheapest first; "upward" lists rely on this order
FEATURES = ("flights", "trading", "briefing", "mail")

# Usage is NOT capped by default (you track it in /admin and abuse.py freezes exploitation). To cap something anyway,
# set PLAN_LIMITS_JSON, e.g. {"basic": {"voice": 100}, "plus": {"flights": 40}}. 0 means "none allowed".
LIMITS: dict[str, dict[str, int]] = {"lite": {"voice": 0, "files": 0}, "basic": {}, "plus": {}}
USAGE_KEYS = ("msgs", "tokens_in", "tokens_out", "voice", "files", "flights", "briefing", "mail")

TRADING_TOOLS = {
    "use_playbook", "scan_results", "backtest_setup", "position_risk", "scenario", "price_levels",
    "latest_news", "market_pulse", "log_trade", "show_portfolio", "journal_add", "journal_close",
    "journal_stats", "set_news_alerts", "watchlist_scan",
}
MAIL_TOOLS = {"mail_list", "mail_read", "mail_draft"}
FEATURE_OF_TOOL = {"flight_search": "flights", "set_briefing": "briefing", "send_briefing_now": "briefing",
                   **{t: "trading" for t in TRADING_TOOLS}, **{t: "mail" for t in MAIL_TOOLS}}


def month_key(now: datetime | None = None) -> str:
    return (now or datetime.now(ZoneInfo(config.TIMEZONE))).strftime("%Y-%m")


def raw_plan(client: dict | None) -> str:
    """The plan the client pays for (ignores a trial)."""
    plan = (client or {}).get("plan")
    return plan if plan in PLANS else "basic"


def trial_active(client: dict | None, now: datetime | None = None) -> bool:
    ends = db.from_iso((client or {}).get("trial_ends_at") or "")
    return bool(ends and ends > (now or db.now_utc()))


def plan_of(client: dict | None) -> str:
    """The plan in force right now: Plus while a free trial runs, otherwise what the client pays for."""
    raw = raw_plan(client)
    if raw == "lite":  # Lite never gets the Plus trial
        return "lite"
    return "plus" if raw == "plus" or trial_active(client) else "basic"


def is_lite(client: dict | None) -> bool:
    return plan_of(client) == "lite"


def upward(plan: str) -> tuple[str, ...]:
    """The plan itself and every dearer one: what a client on `plan` could pay for."""
    return PLANS[PLANS.index(plan):] if plan in PLANS else PLANS


def has_feature(client: dict | None, feature: str) -> bool:
    if is_lite(client):
        return False
    if feature not in config.PLUS_ONLY:
        return True
    return plan_of(client) == "plus"


def limit(plan: str, key: str) -> int | None:
    """Monthly cap, or None when unlimited."""
    base = dict(LIMITS.get(plan, LIMITS["basic"]))
    if config.PLAN_LIMITS_JSON:
        try:
            override = json.loads(config.PLAN_LIMITS_JSON)
            for k, v in (override.get(plan) or {}).items():
                if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                    base[k] = v
        except (ValueError, AttributeError):
            log.warning("PLAN_LIMITS_JSON is not valid JSON; using the built-in limits")
    return base.get(key)


def used(phone: str, key: str, month: str | None = None) -> int:
    with db.conn() as c:
        row = c.execute("SELECT n FROM usage WHERE phone=? AND month=? AND key=?",
                        (phone, month or month_key(), key)).fetchone()
    return int(row["n"]) if row else 0


def usage_table(phone: str) -> dict[str, int]:
    with db.conn() as c:
        rows = c.execute("SELECT key, n FROM usage WHERE phone=? AND month=?", (phone, month_key())).fetchall()
    return {r["key"]: int(r["n"]) for r in rows}


def record(phone: str, key: str, amount: int = 1) -> None:
    with db.conn() as c:
        c.execute(
            "INSERT INTO usage(phone, month, key, n) VALUES(?,?,?,?) "
            "ON CONFLICT(phone, month, key) DO UPDATE SET n = n + excluded.n",
            (phone, month_key(), key, amount))


def within_cap(client: dict, key: str) -> bool:
    """True while the client still has monthly room for this costly action."""
    cap = limit(plan_of(client), key)
    return cap is None or used(client["phone"], key) < cap


def gate(client: dict | None, tool: str) -> str | None:
    """None if the tool may run, else a message for Claude to relay (the client is on Basic)."""
    if is_lite(client):
        return "Lite has no tools. Tell the client briefly that this is not part of the Lite plan and offer plain chat help."
    feature = FEATURE_OF_TOOL.get(tool)
    if feature and not has_feature(client, feature):
        return ("This feature is part of the Plus plan and this client is on Basic. Tell them briefly and politely "
                "that it is a Plus feature, and offer to help with what Basic includes. Do not push.")
    return None


def visible_tools(tools: list[dict], client: dict | None) -> list[dict]:
    """Hide Plus-only tools from Basic clients (also saves tokens)."""
    if is_lite(client):
        return []
    from . import calls  # local: calls imports plans
    return [t for t in tools if gate(client, t.get("name", "")) is None
            and (t.get("name") != "call_prepare" or calls.enabled_for(client))]


def describe(client: dict) -> str:
    """One line for the system prompt."""
    if is_lite(client):
        return ("Plan: Lite. Plain conversation only: no reminders, no stock tools, no live data, no email, no flights, "
                "no files or voice notes. If asked for any of that, say briefly it is part of the Basic or Plus plan.")
    if raw_plan(client) != "plus" and trial_active(client):
        return "Plan: Plus on a free 7-day trial (all features); it reverts to Basic when the trial ends."
    if plan_of(client) == "plus":
        return "Plan: Plus (all features)."
    locked = sorted(f for f in config.PLUS_ONLY if f in FEATURES)
    names = {"flights": "live flight search", "trading": "trading analysis tools", "briefing": "audio morning briefing", "mail": "email assistant"}
    return ("Plan: Basic. Plus-only features: " + ", ".join(names[f] for f in locked) + ". If the client asks for "
            "one of them, say briefly that it is part of the Plus plan.") if locked else "Plan: Basic."
