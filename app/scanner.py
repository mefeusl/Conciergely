"""Nightly chart scan: look for simple, named setups across a fixed universe and keep the results.

Universe = the symbols in app/universe/<market>.txt (a few hundred, editable). The scan runs after each market
closes, never per client message. Each setup also gets a pooled historical test (backtest.py) so a hit always
travels with "how did this pattern behave before", including how few samples there were.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

from . import backtest, bars, db, quality, setups

log = logging.getLogger(__name__)

MARKETS = ("bist", "us")
MAX_UNIVERSE = 400
MAX_STALE_DAYS = 6  # a last bar older than this means a halted/delisted symbol
_DIR = os.path.join(os.path.dirname(__file__), "universe")


def universe(market: str) -> list[str]:
    if market not in MARKETS:
        return []
    out: list[str] = []
    try:
        with open(os.path.join(_DIR, market + ".txt"), encoding="utf-8") as fh:
            for line in fh:
                sym = line.split("#")[0].strip().upper()
                if not sym or not bars.SYMBOL_RE.match(sym):
                    continue
                if market == "bist" and not sym.endswith(".IS"):
                    sym += ".IS"
                if sym not in out:
                    out.append(sym)
    except OSError:
        log.warning("universe file for %s not found", market)
    return out[:MAX_UNIVERSE]


def market_of(symbol: str) -> str:
    return "bist" if symbol.upper().endswith(".IS") else "us"


def _fresh(last_date: str, today: str) -> bool:
    from datetime import date
    try:
        return (date.fromisoformat(today) - date.fromisoformat(last_date)).days <= MAX_STALE_DAYS
    except ValueError:
        return False


def run_scan(market: str, prefetch: bool = True) -> dict:
    """Scan one market. Replaces that market's stored results. Returns a small report."""
    syms = universe(market)
    if not syms:
        return {"market": market, "scanned": 0, "hits": 0}
    if prefetch:
        bars.prefetch(syms)
    today = db.now_utc().strftime("%Y-%m-%d")
    hits: list[tuple] = []
    pooled: dict[str, dict[str, list]] = {s: {} for s in setups.SETUPS}
    scanned = 0
    for sym in syms:
        data = bars.load_cached(sym)
        if not data or len(data) < setups.MIN_BARS or not _fresh(data[-1][0], today):
            continue
        data, _ = quality.window(sym, data)  # a bonus issue/split Yahoo did not adjust would fake signals
        if data is None or len(data) < setups.MIN_BARS:
            continue
        scanned += 1
        d = setups.compute(data)
        last = len(data) - 1
        for name in setups.SETUPS:
            pooled[name][sym] = data
            if setups.detect(name, d, last):
                hits.append((market, name, sym, data[-1][4], json.dumps({"date": data[-1][0]})))
    now = db.now_utc_iso()
    stats = {name: backtest.run_many(items, name) for name, items in pooled.items()}
    with db.conn() as c:
        c.execute("DELETE FROM scan_results WHERE market=?", (market,))
        c.executemany("INSERT INTO scan_results(market,setup,symbol,close,detail,scanned_at) VALUES(?,?,?,?,?,?)",
                      [h + (now,) for h in hits])
        for name, st in stats.items():
            c.execute("""INSERT INTO setup_stats(market,setup,stats,computed_at) VALUES(?,?,?,?)
                         ON CONFLICT(market,setup) DO UPDATE SET stats=excluded.stats, computed_at=excluded.computed_at""",
                      (market, name, json.dumps(stats[name]), now))
    return {"market": market, "scanned": scanned, "hits": len(hits)}


def results(market: str | None = None, setup: str | None = None, limit: int = 15) -> dict:
    limit = max(1, min(int(limit), 30))
    where, args = [], []
    if market:
        where.append("market=?"); args.append(market)
    if setup:
        where.append("setup=?"); args.append(setup)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with db.conn() as c:
        rows = c.execute(f"SELECT market,setup,symbol,close,scanned_at FROM scan_results {clause} "
                         "ORDER BY setup, symbol LIMIT ?", (*args, limit)).fetchall()  # clause built from fixed text
        stat_rows = c.execute("SELECT market,setup,stats,computed_at FROM setup_stats").fetchall()
    stats: dict[str, dict] = {}
    for r in stat_rows:
        if (not market or r["market"] == market) and (not setup or r["setup"] == setup):
            try:
                stats[f"{r['market']}:{r['setup']}"] = json.loads(r["stats"])
            except ValueError:
                continue
    return {
        "hits": [{"market": r["market"], "setup": r["setup"], "symbol": r["symbol"],
                  "close": round(r["close"], 4), "scanned_at": r["scanned_at"]} for r in rows],
        "history_of_each_setup": stats,
        "setups": setups.SETUPS,
        "note": ("Hits are patterns present at the last close, not recommendations. Read history_of_each_setup: "
                 "small 'trades' counts mean the history says little."),
    }


def scan_age_ok(market: str, max_hours: float = 30) -> bool:
    with db.conn() as c:
        row = c.execute("SELECT MAX(scanned_at) AS t FROM scan_results WHERE market=?", (market,)).fetchone()
        stat = c.execute("SELECT MAX(computed_at) AS t FROM setup_stats WHERE market=?", (market,)).fetchone()
    t = db.from_iso((row["t"] or stat["t"] or "")) if (row and (row["t"] or stat["t"])) else None
    return bool(t and db.now_utc() - t < timedelta(hours=max_hours))
