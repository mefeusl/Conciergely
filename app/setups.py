"""Chart setups as plain rules. A setup says "this pattern is present on this bar", nothing more.

Whether a pattern is worth anything is a different question: backtest.py measures how such signals
actually behaved in the past. Rules are deliberately simple and transparent.
"""
from __future__ import annotations

from . import indicators as ind

SETUPS = {
    "breakout": "Close above the highest high of the last 20 days on volume at least 1.5x its 20-day average",
    "oversold_bounce": "RSI(14) climbs back above 30 while the price is above its 200-day average",
    "golden_cross": "50-day average crosses above the 200-day average",
    "squeeze_breakout": "Close above the upper Bollinger band right after the bands were near their narrowest in 6 months",
    "pullback_uptrend": "In an uptrend (50-day above 200-day), a green day that touched the 50-day average with RSI 40-55",
}
MIN_BARS = 210  # the 200-day average needs this much history


def compute(bars: list) -> dict:
    """All indicator series for one symbol, computed once."""
    o = [b[1] for b in bars]
    h = [b[2] for b in bars]
    l = [b[3] for b in bars]
    c = [b[4] for b in bars]
    v = [b[5] for b in bars]
    mid, up, low, width = ind.bollinger(c)
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "dates": [b[0] for b in bars],
            "sma20": ind.sma(c, 20), "sma50": ind.sma(c, 50), "sma200": ind.sma(c, 200),
            "rsi": ind.rsi(c, 14), "atr": ind.atr(h, l, c, 14), "bb_upper": up, "bb_width": width}


def _ok(*values) -> bool:
    return all(x is not None for x in values)


def detect(name: str, d: dict, i: int) -> bool:
    """Is the setup present on bar i? Looks at bar i and earlier only (no peeking ahead)."""
    if i < 1 or i >= len(d["close"]):
        return False
    c, h, l, o, v = d["close"], d["high"], d["low"], d["open"], d["volume"]
    if name == "breakout":
        if i < 21 or not _ok(d["atr"][i]):
            return False
        prior_vol = sum(v[i - 20:i]) / 20
        return c[i] > max(h[i - 20:i]) and prior_vol > 0 and v[i] >= 1.5 * prior_vol
    if name == "oversold_bounce":
        r0, r1, s200 = d["rsi"][i - 1], d["rsi"][i], d["sma200"][i]
        return _ok(r0, r1, s200, d["atr"][i]) and r0 < 30 <= r1 and c[i] > s200
    if name == "golden_cross":
        a0, a1, b0, b1 = d["sma50"][i - 1], d["sma50"][i], d["sma200"][i - 1], d["sma200"][i]
        return _ok(a0, a1, b0, b1, d["atr"][i]) and a0 <= b0 and a1 > b1
    if name == "squeeze_breakout":
        if i < 125 or not _ok(d["bb_upper"][i], d["atr"][i]):
            return False
        recent = [w for w in d["bb_width"][i - 120:i] if w is not None]
        prev = d["bb_width"][i - 1]
        return bool(recent) and prev is not None and prev <= min(recent) * 1.10 and c[i] > d["bb_upper"][i]
    if name == "pullback_uptrend":
        s50, s200, r = d["sma50"][i], d["sma200"][i], d["rsi"][i]
        return (_ok(s50, s200, r, d["atr"][i]) and s50 > s200 and c[i] > s200 and l[i] <= s50 * 1.01
                and c[i] > s50 and c[i] > o[i] and 40 <= r <= 55)
    return False
