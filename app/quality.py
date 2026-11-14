"""Is a price history trustworthy? Catches the jumps that cannot be real trading.

Yahoo does not always adjust Turkish stocks for bonus share issues (bedelsiz) or other corporate actions, so the old
prices look several times higher and every long-range number (52-week range, volatility, trend, scenarios) turns
to nonsense. Borsa Istanbul stops a normal stock from moving more than about 10% in a day, so a bigger one-day
change there is almost certainly a data break. Elsewhere a one-day change of 45% or more is treated the same way.
"""
from __future__ import annotations

BIST_LIMIT = 0.12
DEFAULT_LIMIT = 0.45
MIN_WINDOW = 60  # fewer clean bars than this is not enough to analyse


def limit_for(symbol: str) -> float:
    return BIST_LIMIT if symbol.upper().endswith(".IS") else DEFAULT_LIMIT


def jumps(symbol: str, bars: list) -> list[tuple[int, str, float]]:
    """(index of the bar after the jump, its date, the change) for every impossible one-day move."""
    limit = limit_for(symbol)
    out = []
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1][4], bars[i][4]
        if prev > 0 and abs(cur / prev - 1) > limit:
            out.append((i, bars[i][0], cur / prev - 1))
    return out


def window(symbol: str, bars: list) -> tuple[list | None, dict]:
    """The part of the history that can be analysed, plus a warning for the client.

    Clean history -> (bars, {}). A suspicious jump -> only the bars AFTER the last jump are used. If too few
    remain, the window is None and the caller must not quote long-range numbers."""
    found = jumps(symbol, bars)
    if not found:
        return bars, {}
    start = found[-1][0]
    clean = bars[start:]
    warn = {
        "history_unreliable": True,
        "suspicious_jumps": [{"date": d, "change_pct": round(p * 100, 1)} for _, d, p in found[-3:]],
        "note": ("the price history has a one-day jump that cannot be normal trading, which usually means a bonus "
                 "share issue, split or bad data that the source did not adjust for. Long-range numbers from before "
                 "that date are not used. Tell the client this plainly and suggest checking KAP or their broker."),
    }
    if len(clean) >= MIN_WINDOW:
        warn["analysed_from"] = clean[0][0]
        warn["days_used"] = len(clean)
        return clean, warn
    return None, warn


def _days_apart(a: str, b: str) -> int:
    from datetime import date
    try:
        return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)
    except ValueError:
        return 9999  # an unreadable date never matches


def repair(symbol: str, bars: list, actions: list[dict]) -> tuple[list, list[dict]]:
    """Rescale prices before a jump when a recorded split/bonus issue explains it.

    A jump is repaired only if an action sits within 4 days of it AND the size of the jump is within 15% of the
    action's ratio (a real move of up to 10% rides on top of the split). Returns (new bars, what was repaired)."""
    fixed: list[dict] = []
    out = list(bars)
    for i, date, change in jumps(symbol, bars):
        for act in actions:
            ratio = act["ratio"]
            if ratio <= 0 or _days_apart(date, act["date"]) > 4:
                continue
            if abs((1 / (1 + change)) / ratio - 1) > 0.15:
                continue
            out = [(d, o / ratio, h / ratio, l / ratio, c / ratio, v * ratio) if k < i else (d, o, h, l, c, v)
                   for k, (d, o, h, l, c, v) in enumerate(out)]
            fixed.append({"date": date, "ratio": ratio, "source": "Yahoo's own record of the split/bonus issue"})
            break
    return out, fixed
