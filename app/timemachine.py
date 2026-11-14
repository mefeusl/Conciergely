"""'Time machine': thousands of possible futures for one price, built by reshuffling its own past.

Method (block bootstrap): take the symbol's historical daily log returns, remove their average so the simulation
has no built-in upward drift, then stitch random 5-day blocks of them into many alternative paths. The paths keep
the symbol's real volatility, fat tails and short streaks, but they are NOT a forecast: they cannot know about news,
earnings, regime changes or anything that has not happened before. Stops and targets are checked on closing
prices only, so intraday spikes and overnight gaps are not captured.
"""
from __future__ import annotations

import math
import random

MAX_PATHS = 5000
MAX_HORIZON = 252
MIN_RETURNS = 60
BLOCK = 5


def _pct(sorted_vals: list[float], q: float) -> float:
    k = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def log_returns(closes: list[float]) -> list[float]:
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i] > 0 and closes[i - 1] > 0]


def simulate(closes: list[float], horizon: int = 20, paths: int = 3000, stop: float | None = None,
             target: float | None = None, demean: bool = True, seed: int | None = None) -> dict:
    """Simulate `paths` futures of `horizon` trading days. `stop`/`target` are absolute prices (long trades)."""
    rets = log_returns(closes[-504:])
    if len(rets) < MIN_RETURNS:
        return {"error": "not enough price history to simulate"}
    horizon = max(1, min(int(horizon), MAX_HORIZON))
    paths = max(200, min(int(paths), MAX_PATHS))
    last = closes[-1]
    if demean:
        mu = sum(rets) / len(rets)
        rets = [r - mu for r in rets]
    rnd = random.Random(seed)
    n = len(rets)
    ends, dds = [], []
    hit_target = hit_stop = 0
    ev_sum = 0.0
    risk = (last - stop) if stop is not None else None
    use_levels = stop is not None and target is not None and stop < last < target and risk > 0
    for _ in range(paths):
        level, peak, worst_dd = 0.0, 0.0, 0.0
        outcome = None
        price = last
        steps = 0
        while steps < horizon:
            start = rnd.randrange(n)
            for k in range(BLOCK):
                if steps >= horizon:
                    break
                level += rets[(start + k) % n]
                steps += 1
                price = last * math.exp(level)
                peak = max(peak, level)
                worst_dd = min(worst_dd, math.exp(level - peak) - 1.0)
                if use_levels and outcome is None:
                    if price <= stop:
                        outcome = "stop"
                    elif price >= target:
                        outcome = "target"
        ends.append(price / last - 1.0)
        dds.append(worst_dd)
        if use_levels:
            if outcome == "target":
                hit_target += 1
                ev_sum += (target - last) / risk
            elif outcome == "stop":
                hit_stop += 1
                ev_sum -= 1.0
            else:
                ev_sum += (price - last) / risk
    ends.sort()
    dds.sort()
    out = {
        "paths": paths, "horizon_days": horizon, "start_price": round(last, 4), "drift_removed": demean,
        "end_return_pct": {f"p{int(q * 100)}": round(_pct(ends, q) * 100, 2) for q in (0.05, 0.25, 0.5, 0.75, 0.95)},
        "prob_up_pct": round(sum(1 for e in ends if e > 0) / paths * 100, 1),
        "prob_down_10pct": round(sum(1 for e in ends if e <= -0.10) / paths * 100, 1),
        "prob_up_10pct": round(sum(1 for e in ends if e >= 0.10) / paths * 100, 1),
        "prob_down_20pct": round(sum(1 for e in ends if e <= -0.20) / paths * 100, 1),
        "prob_dip_10pct_along_the_way": round(sum(1 for d in dds if d <= -0.10) / paths * 100, 1),
        "prob_dip_20pct_along_the_way": round(sum(1 for d in dds if d <= -0.20) / paths * 100, 1),
        "max_drawdown_pct": {"median": round(_pct(dds, 0.5) * 100, 2), "bad_case_p95": round(_pct(dds, 0.05) * 100, 2)},
        "daily_volatility_pct": round(_stdev(rets) * 100, 2),
        "note": "reshuffled history, not a forecast; closing prices only, no gaps or news",
    }
    if use_levels:
        out["stop"], out["target"] = stop, target
        out["target_first_pct"] = round(hit_target / paths * 100, 1)
        out["stop_first_pct"] = round(hit_stop / paths * 100, 1)
        out["neither_pct"] = round((paths - hit_target - hit_stop) / paths * 100, 1)
        out["expectancy_r"] = round(ev_sum / paths, 2)
    elif stop is not None or target is not None:
        out["levels_ignored"] = "need stop < current price < target"
    return out


def _stdev(v: list[float]) -> float:
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


HORIZONS = (("1 month", 21), ("3 months", 63), ("12 months", 252))


def horizons(closes: list[float], paths: int = 2000, seed: int | None = None) -> list[dict]:
    """The same simulation at 1, 3 and 12 months, trimmed to what a client reads."""
    out = []
    for label, days in HORIZONS:
        r = simulate(closes, days, paths, seed=seed)
        if "error" in r:
            return []
        e = r["end_return_pct"]
        out.append({"period": label, "trading_days": days, "typical_pct": e["p50"],
                    "range_90pct_of_paths": [e["p5"], e["p95"]], "middle_half_of_paths": [e["p25"], e["p75"]],
                    "chance_higher_pct": r["prob_up_pct"], "chance_down_10pct_or_more": r["prob_down_10pct"],
                    "chance_down_20pct_or_more": r["prob_down_20pct"],
                    "chance_of_a_20pct_dip_along_the_way": r["prob_dip_20pct_along_the_way"],
                    "typical_worst_dip_pct": r["max_drawdown_pct"]["median"]})
    return out


def _chg(closes: list[float], days: int) -> float | None:
    return round((closes[-1] / closes[-1 - days] - 1) * 100, 1) if len(closes) > days else None


def history_profile(bars: list, index_bars: list | None = None) -> dict:
    """What the stock actually did over the past year, in plain numbers. bars: (date, o, h, l, c, v), oldest first."""
    year = bars[-253:]
    closes = [b[4] for b in year]
    if len(closes) < 30:
        return {}
    rets = [(year[i][0], closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1] > 0]
    peak, peak_date, worst, worst_from, worst_to = closes[0], year[0][0], 0.0, year[0][0], year[0][0]
    for b in year:
        if b[4] > peak:
            peak, peak_date = b[4], b[0]
        dd = b[4] / peak - 1
        if dd < worst:
            worst, worst_from, worst_to = dd, peak_date, b[0]
    moves = sorted(rets, key=lambda x: abs(x[1]), reverse=True)[:5]
    daily = [r for _, r in rets]
    mean = sum(daily) / len(daily)
    vol = math.sqrt(sum((x - mean) ** 2 for x in daily) / max(1, len(daily) - 1))
    out = {
        "period": f"{year[0][0]} to {year[-1][0]}",
        "change_1m_pct": _chg(closes, 21), "change_3m_pct": _chg(closes, 63), "change_12m_pct": _chg(closes, len(closes) - 1),
        "from_year_high_pct": round((closes[-1] / max(closes) - 1) * 100, 1),
        "from_year_low_pct": round((closes[-1] / min(closes) - 1) * 100, 1),
        "worst_fall_in_the_year": {"pct": round(worst * 100, 1), "from": worst_from, "to": worst_to},
        "biggest_daily_moves": [{"date": d, "pct": round(r * 100, 1)} for d, r in sorted(moves)],
        "days_up_pct": round(sum(1 for r in daily if r > 0) / len(daily) * 100),
        "typical_daily_swing_pct": round(vol * 100, 2),
    }
    if index_bars:
        idx = {b[0]: b[4] for b in index_bars}
        pairs = [(year[i][0], year[i - 1][0]) for i in range(1, len(year))]
        sr, ir = [], []
        for i, (d, pd) in enumerate(pairs, start=1):
            if d in idx and pd in idx and idx[pd] > 0 and closes[i - 1] > 0:
                sr.append(closes[i] / closes[i - 1] - 1)
                ir.append(idx[d] / idx[pd] - 1)
        if len(sr) >= 60:
            mi = sum(ir) / len(ir)
            var = sum((x - mi) ** 2 for x in ir) / (len(ir) - 1)
            if var > 0:
                cov = sum((a - sum(sr) / len(sr)) * (b - mi) for a, b in zip(sr, ir)) / (len(sr) - 1)
                out["moves_vs_market"] = round(cov / var, 2)
                out["moves_vs_market_note"] = "1.0 = moves like the market index, 1.5 = swings about 50% more, 0.5 = about half"
    return out
