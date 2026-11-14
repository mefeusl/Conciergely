"""One-glance market pulse: the handful of numbers that frame a trading day, from Yahoo (delayed)."""
from __future__ import annotations

from . import markets

PULSE = [
    ("USD/TRY", "USDTRY=X"), ("EUR/TRY", "EURTRY=X"), ("BIST 100", "XU100.IS"), ("S&P 500", "^GSPC"),
    ("Nasdaq", "^IXIC"), ("VIX (fear gauge)", "^VIX"), ("Gold", "GC=F"), ("Brent oil", "BZ=F"),
    ("US 10y yield", "^TNX"),
]


def snapshot(quote_fn=None) -> dict:
    quote_fn = quote_fn or markets.get_quote
    rows, missing = [], []
    for label, sym in PULSE:
        try:
            q = quote_fn(sym)
        except Exception:
            q = None
        if q:
            rows.append({"name": label, "price": round(q["price"], 4),
                         "change_pct": None if q.get("change_pct") is None else round(q["change_pct"], 2)})
        else:
            missing.append(label)
    return {"items": rows, "unavailable": missing, "note": "Yahoo data, about 15 minutes delayed; not a licensed feed"}
