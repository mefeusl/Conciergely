"""Decision journal: write the thesis and a confidence BEFORE the outcome is known, then score the calibration.

Most people remember their wins and forget how sure they were. The journal keeps the honest record and later
shows whether '70% sure' really meant 70%.
"""
from __future__ import annotations

import math
import re
from datetime import timedelta

from . import db

SYMBOL_OK = re.compile(r"^[A-Z0-9.\-=^]{1,15}$")
MAX_OPEN = 50


def add(phone: str, symbol: str, thesis: str, confidence: int, direction: str = "long",
        entry_price: float | None = None, review_days: int = 14) -> dict:
    symbol = (symbol or "").strip().upper().replace(" ", "")
    if not SYMBOL_OK.match(symbol):
        return {"error": "invalid symbol"}
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 1 <= confidence <= 99:
        return {"error": "confidence must be between 1 and 99 (percent sure the idea works out)"}
    direction = (direction or "long").lower()
    if direction not in ("long", "short"):
        return {"error": "direction must be long or short"}
    if entry_price is not None and not (isinstance(entry_price, (int, float)) and not isinstance(entry_price, bool)
                                        and math.isfinite(entry_price) and entry_price > 0):
        return {"error": "entry_price must be a positive number"}
    thesis = re.sub(r"\s+", " ", (thesis or "")).strip()[:400]
    if not thesis:
        return {"error": "write the reason in a sentence"}
    review_days = max(1, min(int(review_days), 365))
    review_at = db.now_utc() + timedelta(days=review_days)
    with db.conn() as c:
        if c.execute("SELECT COUNT(*) FROM journal WHERE phone=? AND status='open'", (phone,)).fetchone()[0] >= MAX_OPEN:
            return {"error": "too many open entries; close some first"}
        cur = c.execute("""INSERT INTO journal(phone,symbol,direction,thesis,confidence,entry_price,review_on,created_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (phone, symbol, direction, thesis, int(confidence), entry_price,
                         review_at.strftime("%Y-%m-%d"), db.now_utc_iso()))
        entry_id = cur.lastrowid
    db.add_reminder(phone, f"📓 #{entry_id} {symbol}: {thesis[:120]}", db.to_iso(review_at))
    return {"ok": True, "id": entry_id, "review_on": review_at.strftime("%Y-%m-%d")}


def close(phone: str, entry_id: int, exit_price: float) -> dict:
    if not (isinstance(exit_price, (int, float)) and not isinstance(exit_price, bool)
            and math.isfinite(exit_price) and exit_price > 0):
        return {"error": "exit_price must be a positive number"}
    with db.conn() as c:
        row = c.execute("SELECT * FROM journal WHERE id=? AND phone=?", (entry_id, phone)).fetchone()
        if not row:
            return {"error": "no such journal entry"}
        if row["status"] != "open":
            return {"error": "already closed"}
        if not row["entry_price"]:
            return {"error": "this entry has no entry price; tell me the entry price first"}
        raw = (exit_price / row["entry_price"] - 1) * 100
        pct = raw if row["direction"] == "long" else -raw
        c.execute("UPDATE journal SET status='closed', exit_price=?, outcome_pct=?, closed_at=? WHERE id=?",
                  (exit_price, round(pct, 2), db.now_utc_iso(), entry_id))
    return {"ok": True, "id": entry_id, "outcome_pct": round(pct, 2), "worked": pct > 0, "confidence": row["confidence"]}


def set_entry_price(phone: str, entry_id: int, price: float) -> bool:
    if not (isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price) and price > 0):
        return False
    with db.conn() as c:
        return c.execute("UPDATE journal SET entry_price=? WHERE id=? AND phone=? AND status='open'",
                         (price, entry_id, phone)).rowcount > 0


def list_open(phone: str) -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT id,symbol,direction,thesis,confidence,entry_price,review_on FROM journal "
                         "WHERE phone=? AND status='open' ORDER BY id", (phone,)).fetchall()
    return [dict(r) for r in rows]


def stats(phone: str) -> dict:
    with db.conn() as c:
        rows = c.execute("SELECT confidence, outcome_pct FROM journal WHERE phone=? AND status='closed' "
                         "AND outcome_pct IS NOT NULL", (phone,)).fetchall()
    n = len(rows)
    if n == 0:
        return {"closed_entries": 0, "note": "no closed entries yet; close an entry with its exit price to start scoring"}
    wins = [1 if r["outcome_pct"] > 0 else 0 for r in rows]
    probs = [r["confidence"] / 100 for r in rows]
    brier = sum((p - w) ** 2 for p, w in zip(probs, wins)) / n
    avg_conf = sum(probs) / n * 100
    hit = sum(wins) / n * 100
    buckets = {}
    for lo, hi, label in ((1, 50, "under 50%"), (50, 70, "50-69%"), (70, 100, "70%+")):
        sel = [(r["confidence"], w) for r, w in zip(rows, wins) if lo <= r["confidence"] < hi or (hi == 100 and r["confidence"] == 100)]
        if sel:
            buckets[label] = {"entries": len(sel), "worked_pct": round(sum(w for _, w in sel) / len(sel) * 100, 1)}
    out = {"closed_entries": n, "worked_pct": round(hit, 1), "average_confidence_pct": round(avg_conf, 1),
           "brier_score": round(brier, 3), "by_confidence": buckets,
           "avg_outcome_pct": round(sum(r["outcome_pct"] for r in rows) / n, 2),
           "note": "Brier: 0 is perfect, 0.25 is what always saying 50% scores; lower is better"}
    if n < 10:
        out["warning"] = f"only {n} closed entries; treat this as a first impression, not a verdict"
    elif avg_conf - hit > 10:
        out["reading"] = "your confidence has run higher than your results (overconfidence)"
    elif hit - avg_conf > 10:
        out["reading"] = "your results have beaten your confidence (underconfidence)"
    else:
        out["reading"] = "confidence and results are roughly in line"
    return out
