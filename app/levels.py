"""A plain-numbers snapshot of where a price sits: trend, momentum, volatility, nearby swing levels."""
from __future__ import annotations

import math

from . import indicators as ind

FRACTAL = 3  # a swing high/low is the extreme of 2*FRACTAL+1 bars


def swings(highs: list[float], lows: list[float]) -> tuple[list[float], list[float]]:
    """Swing highs and lows (oldest first). The last FRACTAL bars cannot be confirmed yet and are skipped."""
    hs, ls = [], []
    for i in range(FRACTAL, len(highs) - FRACTAL):
        if highs[i] == max(highs[i - FRACTAL:i + FRACTAL + 1]):
            hs.append(highs[i])
        if lows[i] == min(lows[i - FRACTAL:i + FRACTAL + 1]):
            ls.append(lows[i])
    return hs, ls


def snapshot(bars: list) -> dict:
    if len(bars) < 60:
        return {"error": "not enough history"}
    h = [b[2] for b in bars]
    l = [b[3] for b in bars]
    c = [b[4] for b in bars]
    last = c[-1]
    s50, s200 = ind.sma(c, 50)[-1], ind.sma(c, 200)[-1]
    r = ind.rsi(c, 14)[-1]
    a = ind.atr(h, l, c, 14)[-1]
    year_h, year_l = max(h[-252:]), min(l[-252:])
    rets = [math.log(c[i] / c[i - 1]) for i in range(max(1, len(c) - 60), len(c))]
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)) * math.sqrt(252) * 100
    sh, sl = swings(h[-180:], l[-180:])
    supports = sorted({round(x, 4) for x in sl if x < last}, reverse=True)[:2]
    resistances = sorted({round(x, 4) for x in sh if x > last})[:2]
    if s50 is None:
        trend = "unknown"
    elif s200 is None:
        trend = "above 50-day average" if last > s50 else "below 50-day average"
    elif last > s50 > s200:
        trend = "uptrend (price above 50-day above 200-day)"
    elif last < s50 < s200:
        trend = "downtrend (price below 50-day below 200-day)"
    else:
        trend = "mixed (averages and price disagree)"
    out = {
        "last_close": round(last, 4), "last_date": bars[-1][0], "trend": trend,
        "sma50": round(s50, 4) if s50 else None, "sma200": round(s200, 4) if s200 else None,
        "rsi14": round(r, 1) if r is not None else None,
        "rsi_reading": None if r is None else ("overbought zone (>70)" if r > 70 else "oversold zone (<30)" if r < 30 else "neutral"),
        "atr14": round(a, 4) if a else None, "atr_pct": round(a / last * 100, 2) if a else None,
        "annualized_volatility_pct": round(vol, 1),
        "change_1d_pct": round((c[-1] / c[-2] - 1) * 100, 2),
        "change_5d_pct": round((c[-1] / c[-6] - 1) * 100, 2) if len(c) > 6 else None,
        "change_1m_pct": round((c[-1] / c[-22] - 1) * 100, 2) if len(c) > 22 else None,
        "from_52w_high_pct": round((last / year_h - 1) * 100, 1), "from_52w_low_pct": round((last / year_l - 1) * 100, 1),
        "nearby_supports": supports, "nearby_resistances": resistances,
    }
    if a:
        out["study_levels"] = {"stop_2atr_below": round(last - 2 * a, 4), "target_3atr_above": round(last + 3 * a, 4),
                               "note": "volatility-based reference points for studying a trade, not instructions"}
    return out
