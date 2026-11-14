"""A simple trade ledger kept for the client: average-cost positions and profit/loss.

It records what the client says they did. It never touches a broker. Long positions only; selling more than is
held is refused so a typo cannot corrupt the ledger. P/L is per currency (never added across currencies).
"""
from __future__ import annotations

import math
import re
from datetime import date

from . import db

SYMBOL_OK = re.compile(r"^[A-Z0-9.\-=^]{1,15}$")
MAX_TRADES = 2000


def _num(x, positive=True) -> bool:
    return (isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
            and (x > 0 if positive else x >= 0))


def add_trade(phone: str, symbol: str, side: str, qty: float, price: float, fee: float = 0.0,
              traded_on: str = "", currency: str = "") -> dict:
    symbol = (symbol or "").strip().upper().replace(" ", "")
    side = (side or "").strip().lower()
    if not SYMBOL_OK.match(symbol):
        return {"error": "invalid symbol"}
    if side not in ("buy", "sell"):
        return {"error": "side must be buy or sell"}
    if not (_num(qty) and _num(price) and _num(fee, positive=False)):
        return {"error": "qty and price must be positive numbers and fee zero or more"}
    try:
        day = date.fromisoformat(traded_on) if traded_on else db.now_utc().date()
    except ValueError:
        return {"error": "traded_on must look like 2026-10-24"}
    if day > db.now_utc().date():
        return {"error": "traded_on is in the future"}
    currency = re.sub(r"[^A-Z]", "", (currency or "").upper())[:5]
    with db.conn() as c:
        if c.execute("SELECT COUNT(*) FROM trades WHERE phone=?", (phone,)).fetchone()[0] >= MAX_TRADES:
            return {"error": "trade ledger is full"}
        if side == "sell":
            held = _held(c, phone, symbol)
            if qty > held + 1e-9:
                return {"error": f"you hold {held:g} of {symbol}; cannot sell {qty:g}"}
        cur = c.execute("""INSERT INTO trades(phone,symbol,side,qty,price,fee,traded_on,currency,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (phone, symbol, side, qty, price, fee, day.isoformat(), currency, db.now_utc_iso()))
        return {"ok": True, "id": cur.lastrowid, "symbol": symbol, "side": side, "qty": qty, "price": price}


def _held(c, phone: str, symbol: str) -> float:
    rows = c.execute("SELECT side, qty FROM trades WHERE phone=? AND symbol=?", (phone, symbol)).fetchall()
    return sum(r["qty"] if r["side"] == "buy" else -r["qty"] for r in rows)


def positions(phone: str) -> dict:
    """Average-cost positions and realised P/L, replayed from the ledger in date order."""
    with db.conn() as c:
        rows = c.execute("SELECT * FROM trades WHERE phone=? ORDER BY traded_on, id", (phone,)).fetchall()
    book: dict[str, dict] = {}
    for r in rows:
        p = book.setdefault(r["symbol"], {"qty": 0.0, "cost": 0.0, "realized": 0.0, "fees": 0.0, "currency": ""})
        p["currency"] = r["currency"] or p["currency"]
        p["fees"] += r["fee"]
        if r["side"] == "buy":
            p["qty"] += r["qty"]
            p["cost"] += r["qty"] * r["price"] + r["fee"]
        else:
            avg = p["cost"] / p["qty"] if p["qty"] else 0.0
            p["realized"] += r["qty"] * (r["price"] - avg) - r["fee"]
            p["cost"] -= avg * r["qty"]
            p["qty"] -= r["qty"]
            if p["qty"] < 1e-9:
                p["qty"], p["cost"] = 0.0, 0.0
    return book


def summary(phone: str, quote_fn=None) -> dict:
    """Open positions with unrealised P/L (when quotes are available) and realised P/L, per currency."""
    book = positions(phone)
    open_pos, realized_by_cur = [], {}
    for sym, p in sorted(book.items()):
        cur = p["currency"]
        realized_by_cur[cur] = realized_by_cur.get(cur, 0.0) + p["realized"]
        if p["qty"] <= 0:
            continue
        item = {"symbol": sym, "qty": round(p["qty"], 6), "avg_cost": round(p["cost"] / p["qty"], 4), "currency": cur}
        quote = None
        if quote_fn:
            try:
                quote = quote_fn(sym)
            except Exception:
                quote = None
        if quote:
            price = quote["price"]
            item["last_price"] = round(price, 4)
            item["unrealized"] = round(p["qty"] * price - p["cost"], 2)
            item["unrealized_pct"] = round((price * p["qty"] / p["cost"] - 1) * 100, 2) if p["cost"] else None
            item["currency"] = cur or quote.get("currency", "")
        open_pos.append(item)
    return {"positions": open_pos,
            "realized_by_currency": {k or "?": round(v, 2) for k, v in realized_by_cur.items() if abs(v) > 1e-9},
            "note": "average-cost method from the trades you told me about; quotes are delayed; not tax advice"}


def delete_trade(phone: str, trade_id: int) -> bool:
    """Remove one trade. Refused if it would leave a later sell without enough shares."""
    with db.conn() as c:
        row = c.execute("SELECT * FROM trades WHERE id=? AND phone=?", (trade_id, phone)).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM trades WHERE id=?", (trade_id,))
        held = 0.0
        for r in c.execute("SELECT side, qty FROM trades WHERE phone=? AND symbol=? ORDER BY traded_on, id",
                           (phone, row["symbol"])):
            held += r["qty"] if r["side"] == "buy" else -r["qty"]
            if held < -1e-9:
                raise ValueError("would break later sells")
        return True
