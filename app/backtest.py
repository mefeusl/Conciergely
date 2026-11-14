"""Historical test of a setup: what happened after the pattern appeared in the past?

Rules of the test, chosen to avoid flattering the results:
  * the signal is seen at the close; the trade starts at the NEXT day's open (no peeking ahead);
  * stop = entry - stop_atr x ATR, target = entry + target_atr x ATR, long trades only;
  * if one day touches both stop and target, the stop is assumed to hit first;
  * a gap through the stop fills at the (worse) open price;
  * a round-trip cost (default 0.1% of the entry) is deducted;
  * a trade that hits neither is closed at the close of the last day of the holding window;
  * signals while a trade is open are skipped (no overlapping trades).
A result is only as good as its sample: below 30 trades it is marked unreliable.
"""
from __future__ import annotations

import statistics

from . import setups

RELIABLE_TRADES = 30


def run(bars: list, setup: str, horizon: int = 10, stop_atr: float = 2.0, target_atr: float = 3.0,
        cost_pct: float = 0.1, data: dict | None = None) -> list[dict]:
    """All simulated trades for one symbol. Each is {signal_date, entry, exit, outcome, r, return_pct, days}."""
    if len(bars) < setups.MIN_BARS or setup not in setups.SETUPS:
        return []
    d = data or setups.compute(bars)
    n = len(bars)
    trades: list[dict] = []
    i = 1
    while i < n - 1:
        if not setups.detect(setup, d, i):
            i += 1
            continue
        entry = d["open"][i + 1]
        risk = stop_atr * d["atr"][i]
        if entry <= 0 or risk <= 0 or entry - risk <= 0:
            i += 1
            continue
        stop, target = entry - risk, entry + target_atr * d["atr"][i]
        last = min(i + horizon, n - 1)
        exit_price, outcome, end = d["close"][last], "timeout", last
        for j in range(i + 1, last + 1):
            if d["open"][j] <= stop:  # gapped through the stop
                exit_price, outcome, end = d["open"][j], "stop", j
                break
            if d["low"][j] <= stop:
                exit_price, outcome, end = stop, "stop", j
                break
            if d["high"][j] >= target:
                exit_price, outcome, end = target, "target", j
                break
        gross = exit_price - entry - entry * cost_pct / 100.0
        trades.append({"signal_date": d["dates"][i], "entry": entry, "exit": exit_price, "outcome": outcome,
                       "r": gross / risk, "return_pct": gross / entry * 100.0, "days": end - i})
        i = end + 1
    return trades


def summarize(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "reliable": False, "note": "no historical signals to measure"}
    rs = [t["r"] for t in trades]
    wins = sum(1 for r in rs if r > 0)
    share = lambda kind: sum(1 for t in trades if t["outcome"] == kind) / n * 100.0  # noqa: E731
    return {
        "trades": n,
        "win_rate_pct": round(wins / n * 100.0, 1),
        "target_hit_pct": round(share("target"), 1),
        "stop_hit_pct": round(share("stop"), 1),
        "timeout_pct": round(share("timeout"), 1),
        "avg_r": round(sum(rs) / n, 2),
        "median_r": round(statistics.median(rs), 2),
        "best_r": round(max(rs), 2),
        "worst_r": round(min(rs), 2),
        "avg_return_pct": round(sum(t["return_pct"] for t in trades) / n, 2),
        "avg_days": round(sum(t["days"] for t in trades) / n, 1),
        "reliable": n >= RELIABLE_TRADES,
        "note": ("past behaviour of this rule, with costs deducted; it does not predict the next trade"
                 + ("" if n >= RELIABLE_TRADES else f"; only {n} trades, too few to rely on")),
    }


def run_many(items: dict[str, list], setup: str, **kw) -> dict:
    """Pool the trades of many symbols into one summary."""
    pooled: list[dict] = []
    used = 0
    for bars in items.values():
        found = run(bars, setup, **kw)
        used += 1 if found else 0
        pooled.extend(found)
    out = summarize(pooled)
    out["symbols_with_signals"] = used
    return out
