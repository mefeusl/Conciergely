"""Daily price history (open, high, low, close, volume) from Yahoo Finance, with a small file cache.

A bar is a tuple (date, open, high, low, close, volume). Data is free and unofficial: it can be late,
wrong or missing, and Yahoo may throttle bulk downloads, so everything here fails soft (returns None).
The two functions that touch the network, _download and _download_one, are the only ones that need yfinance.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time

from . import config

log = logging.getLogger(__name__)

SYMBOL_RE = re.compile(r"^[A-Z0-9.\-=^]{1,15}$")
CACHE_SECONDS = 6 * 3600
BATCH = 40
MIN_USEFUL = 30  # fewer bars than this is not worth analysing


def _cache_path(symbol: str) -> str:
    folder = os.path.join(os.path.dirname(config.DB_PATH), "cache")
    return os.path.join(folder, re.sub(r"[^A-Z0-9]", "_", symbol) + ".json")


def _read_cache(symbol: str, max_age: float | None) -> list | None:
    path = _cache_path(symbol)
    try:
        if max_age is not None and time.time() - os.path.getmtime(path) > max_age:
            return None
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)
        return [tuple(r) for r in rows] if isinstance(rows, list) and rows else None
    except (OSError, ValueError):
        return None


def _write_cache(symbol: str, bars: list) -> None:
    path = _cache_path(symbol)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(bars, fh)
        os.replace(tmp, path)
    except OSError:
        log.warning("could not write the price cache for %s", symbol)


def clean(rows) -> list:
    """Keep only sane bars, oldest first: positive prices, high >= low, no NaN."""
    out = []
    for r in rows:
        try:
            date, o, h, l, c, v = r
            o, h, l, c, v = float(o), float(h), float(l), float(c), float(v or 0)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(x) for x in (o, h, l, c, v)) or min(o, h, l, c) <= 0 or h < l:
            continue
        out.append((str(date)[:10], o, max(h, o, c), min(l, o, c), c, v))
    return out


def _frame_to_bars(frame) -> list:
    rows = []
    for idx, row in frame.iterrows():
        rows.append((idx.strftime("%Y-%m-%d"), row.get("Open"), row.get("High"), row.get("Low"),
                     row.get("Close"), row.get("Volume")))
    return clean(rows)


def _download(symbols: list[str]) -> dict[str, list]:
    """Fetch several symbols in one request. Network call (yfinance)."""
    import yfinance as yf

    out: dict[str, list] = {}
    if not symbols:
        return out
    try:
        frame = yf.download(symbols, period="2y", interval="1d", group_by="ticker", auto_adjust=True,
                            progress=False, threads=True)
    except Exception as exc:
        log.warning("bulk price download failed: %s", type(exc).__name__)
        return out
    for sym in symbols:
        try:
            sub = frame[sym] if len(symbols) > 1 else (frame[sym] if sym in getattr(frame, "columns", []) else frame)
            out[sym] = _frame_to_bars(sub.dropna(subset=["Close"]))
        except Exception:
            continue
    return out


def _download_one(symbol: str) -> list:
    """Fetch one symbol on its own. Network call (yfinance)."""
    import yfinance as yf

    try:
        return _frame_to_bars(yf.Ticker(symbol).history(period="2y", interval="1d", auto_adjust=True))
    except Exception as exc:
        log.debug("price history failed for %s: %s", symbol, exc)
        return []


_actions_cache: dict[str, tuple[float, list]] = {}


def corporate_actions(symbol: str) -> list[dict]:
    """Splits / bonus issues Yahoo has on record: [{'date': 'YYYY-MM-DD', 'ratio': 2.0}], ratio = new shares per old one.
    Network call (yfinance); fails soft and is cached for a day."""
    hit = _actions_cache.get(symbol)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    out: list[dict] = []
    try:
        import yfinance as yf
        for idx, ratio in yf.Ticker(symbol).splits.items():
            if ratio and float(ratio) > 0 and abs(float(ratio) - 1) > 1e-6:
                out.append({"date": idx.strftime("%Y-%m-%d"), "ratio": float(ratio)})
    except Exception as exc:  # noqa: BLE001 - optional extra evidence
        log.debug("corporate actions failed for %s: %s", symbol, exc)
        return []
    _actions_cache[symbol] = (time.time(), out)
    return out


def normalize(symbol: str) -> str:
    return (symbol or "").strip().upper().replace(" ", "")


def _candidates(sym: str) -> list[str]:
    return [sym] if any(ch in sym for ch in ".=-^") else [sym, sym + ".IS"]


def get_bars(symbol: str, max_age: float = CACHE_SECONDS) -> tuple[str, list] | None:
    """Returns (resolved symbol, bars) or None. A bare symbol is tried as given, then as a Borsa Istanbul ticker."""
    sym = normalize(symbol)
    if not SYMBOL_RE.match(sym):
        return None
    for cand in _candidates(sym):
        cached = _read_cache(cand, max_age)
        if cached and len(cached) >= MIN_USEFUL:
            return cand, cached
    for cand in _candidates(sym):
        bars = _download_one(cand)
        if len(bars) >= MIN_USEFUL:
            _write_cache(cand, bars)
            return cand, bars
    for cand in _candidates(sym):  # Yahoo is down: an old copy beats nothing
        stale = _read_cache(cand, None)
        if stale and len(stale) >= MIN_USEFUL:
            return cand, stale
    return None


def prefetch(symbols: list[str], max_age: float = CACHE_SECONDS) -> int:
    """Warm the cache for many exact symbols using bulk downloads. Returns how many symbols are now cached."""
    need = [s for s in symbols if SYMBOL_RE.match(s) and not _read_cache(s, max_age)]
    for i in range(0, len(need), BATCH):
        chunk = need[i:i + BATCH]
        for sym, bars in _download(chunk).items():
            if len(bars) >= MIN_USEFUL:
                _write_cache(sym, bars)
    return sum(1 for s in symbols if _read_cache(s, None))


def load_cached(symbol: str) -> list | None:
    """Bars straight from the cache (any age), for scans that prefetched first."""
    return _read_cache(symbol, None)
