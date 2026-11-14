"""Price lookups via Yahoo Finance (yfinance), plus market-hours logic.

Borsa Istanbul tickers use the ".IS" suffix (THYAO.IS). US tickers are plain (NVDA).
Yahoo data is free but delayed (~15 min for BIST/US) and is not a licensed feed.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# Delayed data keeps updating after the closing bell, so we keep checking a little longer.
_BIST = (ZoneInfo("Europe/Istanbul"), time(10, 0), time(18, 20))
_US = (ZoneInfo("America/New_York"), time(9, 30), time(16, 20))


ALIASES = {  # what a client says -> the Yahoo symbol. Gold is USD per ounce; there is no gram-gold symbol.
    "DOLAR": "USDTRY=X", "USD": "USDTRY=X", "USDTRY": "USDTRY=X", "EURO": "EURTRY=X", "EUR": "EURTRY=X", "EURTRY": "EURTRY=X",
    "STERLIN": "GBPTRY=X", "GBP": "GBPTRY=X", "GBPTRY": "GBPTRY=X", "ALTIN": "GC=F", "ONSALTIN": "GC=F", "ONS": "GC=F", "XAU": "GC=F",
    "XAUUSD": "GC=F",
}


def normalize_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper().replace(" ", "")
    return ALIASES.get(sym, sym)


def _is_bad(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _fetch(symbol: str) -> dict | None:
    import yfinance as yf  # imported lazily: only needed when a price is really requested

    try:
        info = yf.Ticker(symbol).fast_info
        price = info["last_price"]
        prev = info["previous_close"]
        currency = info["currency"]
    except Exception as exc:  # unknown symbol, network error, library change
        log.debug("quote fetch failed for %s: %s", symbol, exc)
        return None
    if _is_bad(price) or price <= 0:
        return None
    change = None
    if not _is_bad(prev) and prev:
        change = (price - prev) / prev * 100.0
    return {
        "symbol": symbol,
        "price": float(price),
        "previous_close": None if _is_bad(prev) else float(prev),
        "change_pct": change,
        "currency": currency or "",
    }


def get_quote(symbol: str) -> dict | None:
    """Return a quote dict, or None. A bare symbol is tried as-is, then with '.IS' (Borsa Istanbul)."""
    sym = normalize_symbol(symbol)
    if not sym:
        return None
    candidates = [sym]
    if not any(ch in sym for ch in ".=-^"):
        candidates.append(sym + ".IS")
    for cand in candidates:
        quote = _fetch(cand)
        if quote:
            return quote
    return None


def market_is_open(symbol: str, now: datetime | None = None) -> bool:
    """Is the symbol's exchange (about) open? Public holidays are not modelled."""
    now = now or datetime.now(timezone.utc)
    sym = normalize_symbol(symbol)
    if "=" in sym or "-" in sym:  # currencies and crypto trade around the clock
        return True
    tz, opens, closes = _BIST if sym.endswith(".IS") else _US
    local = now.astimezone(tz)
    if local.weekday() >= 5:
        return False
    return opens <= local.time() <= closes
