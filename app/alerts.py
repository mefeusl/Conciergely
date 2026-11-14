"""Stock limit alerts.

Each watchlist row can have an upper price limit, a lower price limit and/or a daily move threshold (%).
An alert fires ONCE when the price crosses a limit, and re-arms after the price is back inside the limits.
The daily-move alert fires at most once per day. Prices come from Yahoo (about 15 minutes delayed).
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config, db, i18n, jobs, markets

log = logging.getLogger(__name__)


def evaluate(w: dict, price: float, change_pct: float | None, today: str) -> tuple[list[str], str, str]:
    """Decide which alerts to send. Returns (events, new_state, new_pct_alert_date).

    events are any of "above", "below", "move". state is "above" | "below" | "inside".
    """
    upper, lower = w.get("upper"), w.get("lower")
    if upper is not None and price >= upper:
        zone = "above"
    elif lower is not None and price <= lower:
        zone = "below"
    else:
        zone = "inside"

    events: list[str] = []
    state = w.get("state") or "unknown"
    if zone != state:
        if zone in ("above", "below"):
            events.append(zone)
        state = zone

    pct_date = w.get("pct_alert_date") or ""
    threshold = w.get("move_pct")
    if threshold and change_pct is not None and abs(change_pct) >= threshold and pct_date != today:
        events.append("move")
        pct_date = today
    return events, state, pct_date


def format_alert(lang: str, event: str, w: dict, quote: dict) -> str:
    base = {"symbol": w["symbol"], "price": f"{quote['price']:.2f}", "cur": quote.get("currency", "")}
    if event == "above":
        return i18n.t(lang, "alert_above", limit=f"{w['upper']:g}", **base)
    if event == "below":
        return i18n.t(lang, "alert_below", limit=f"{w['lower']:g}", **base)
    chg = quote.get("change_pct") or 0.0
    return i18n.t(lang, "alert_move", pct=f"{w['move_pct']:g}", chg=f"{chg:+.2f}%", **base)


def run_cycle(now: datetime | None = None) -> int:
    """Check every active watchlist once. Returns how many alert messages were queued."""
    now = now or db.now_utc()
    today = now.astimezone(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")
    quotes: dict[str, dict | None] = {}
    queued = 0
    for w in db.all_active_watches():
        sym = w["symbol"]
        if not markets.market_is_open(sym, now):
            continue
        if sym not in quotes:
            try:
                quotes[sym] = markets.get_quote(sym)
            except Exception:  # one bad symbol must not stop the other alerts
                log.exception("quote failed for %s", sym)
                quotes[sym] = None
        quote = quotes[sym]
        if not quote:
            continue
        events, state, pct_date = evaluate(w, quote["price"], quote.get("change_pct"), today)
        if state != w["state"] or pct_date != w["pct_alert_date"]:
            db.update_watch_state(w["id"], state, pct_date)
        for event in events:
            text = format_alert(w.get("lang") or "tr", event, w, quote)
            jobs.enqueue("send", w["phone"], {"text": text, "proactive": True, "respect_quiet": True})
            queued += 1
    return queued
