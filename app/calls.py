"""Calling add-on. OFF by default, behind two switches: calling_on (global, /admin) and clients.calls_on (per client).

The assistant can only prepare a call (call_prepare). The call is placed when the client answers 'ara' / 'call',
checked in code before the AI is involved. Providers are small adapters:
  * 'test'    never dials; writes what would have happened to the call log (use it to try the flow);
  * 'webhook' POSTs {to, message, language, client} as JSON to your URL with 'Authorization: Bearer <key>'
              (point it at whatever service you choose later; the key is stored sealed in the settings table).
Guards: emergency numbers blocked, E.164 numbers only, message length cap, daily limit per client, every call logged.
"""
from __future__ import annotations

import json
import logging
import re

import requests

from . import config, db, plans, settings, vault

log = logging.getLogger(__name__)

NUMBER_RE = re.compile(r"^\+[1-9]\d{7,14}$")
EMERGENCY = {"112", "110", "155", "156", "122", "177", "911", "999", "+90112", "+90110", "+90155", "+90156", "+90122", "+90177"}
MESSAGE_LIMIT = 600
CALL_WORDS = {"ara", "arayin", "ara lutfen", "call", "call now", "yes call", "evet ara"}
CANCEL_WORDS = {"vazgec", "iptal", "cancel", "arama"}

TEXT = {
    "tr": {"off": "Arama özelliği şu an kapalı.", "prepared": "Hazır. {to} numarasına şu mesajı okuyacağım:\n“{message}”\n\nAramamı ister misiniz? “ara” yazın, vazgeçmek için “iptal”.",
           "placed": "Arama başlatıldı: {to}.", "test": "Test modu: arama yapılmadı, yalnızca kaydedildi ({to}).",
           "failed": "Arama başlatılamadı. Biraz sonra tekrar deneyin.", "limit": "Bugünkü arama hakkınız doldu.",
           "dropped": "Arama iptal edildi."},
    "en": {"off": "Calling is switched off right now.", "prepared": "Ready. I will read this to {to}:\n“{message}”\n\nShall I call? Write “call”, or “cancel” to drop it.",
           "placed": "Call started: {to}.", "test": "Test mode: no call was made, it was only logged ({to}).",
           "failed": "The call could not be started. Please try again shortly.", "limit": "Today's call allowance is used up.",
           "dropped": "Call cancelled."},
}


def t(lang: str, key: str, **kw) -> str:
    return TEXT["en" if lang == "en" else "tr"][key].format(**kw)


def enabled_for(client: dict | None) -> bool:
    return bool(client and client.get("calls_on") and settings.calling_on() and plans.plan_of(client) == "plus")


def normalise(number: str) -> str | None:
    n = db.norm_phone(number or "")
    if not NUMBER_RE.match(n) or n in EMERGENCY or any(n.endswith(e) and len(n) <= len(e) + 3 for e in ("112", "110", "155")):
        return None
    return n


def calls_today(phone: str) -> int:
    since = db.now_utc().strftime("%Y-%m-%dT00:00:00")
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM call_log WHERE phone=? AND at>=? AND status IN ('placed','test')",
                         (phone, since)).fetchone()["n"]


def prepare(phone: str, number: str, message: str, lang: str) -> dict:
    client = db.get_client(phone)
    if not enabled_for(client):
        return {"error": "calling is switched off for this client; say so in one line"}
    n = normalise(number)
    if not n:
        return {"error": "that is not a valid phone number with country code (emergency numbers are not allowed)"}
    message = " ".join((message or "").split())[:MESSAGE_LIMIT]
    if not message:
        return {"error": "what should be said on the call?"}
    if config.CALLS_PER_DAY and calls_today(phone) >= config.CALLS_PER_DAY:
        return {"error": "the client's call allowance for today is used up"}
    with db.conn() as c:
        c.execute("INSERT INTO call_drafts(phone, to_number, message, lang, created_at) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(phone) DO UPDATE SET to_number=excluded.to_number, message=excluded.message, "
                  "lang=excluded.lang, created_at=excluded.created_at", (phone, n, message, lang, db.now_utc_iso()))
    return {"ok": True, "to": n, "message": message, "placed": False,
            "note": "NOT called yet. Show the number and the message and tell the client to reply 'ara' to place it or 'iptal'."}


def _log(phone: str, number: str, message: str, status: str, detail: str = "") -> None:
    with db.conn() as c:
        c.execute("INSERT INTO call_log(phone, at, to_number, message, status, detail) VALUES(?,?,?,?,?,?)",
                  (phone, db.now_utc_iso(), number, message, status, detail[:200]))


def _post(url: str, key: str, payload: dict):
    return requests.post(url, json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=20)


def _key() -> str:
    blob = settings.get("calling_key", "")
    if not blob:
        return ""
    try:
        return vault.open_("settings", blob)
    except vault.VaultError:
        return ""


def place(phone: str) -> tuple[str, str]:
    """(status, number). status: placed | test | failed | off | limit | none."""
    client = db.get_client(phone)
    with db.conn() as c:
        row = c.execute("SELECT * FROM call_drafts WHERE phone=?", (phone,)).fetchone()
        if row:
            c.execute("DELETE FROM call_drafts WHERE phone=?", (phone,))  # a draft can be used once
    if not row:
        return "none", ""
    draft = dict(row)
    if not enabled_for(client):
        return "off", draft["to_number"]
    if config.CALLS_PER_DAY and calls_today(phone) >= config.CALLS_PER_DAY:
        return "limit", draft["to_number"]
    provider = settings.get("calling_provider", "test")
    if provider == "webhook":
        url, key = settings.get("calling_url", ""), _key()
        if not url.startswith("https://") or not key:
            _log(phone, draft["to_number"], draft["message"], "failed", "webhook not configured")
            return "failed", draft["to_number"]
        try:
            r = _post(url, key, {"to": draft["to_number"], "message": draft["message"], "language": draft["lang"], "client": phone})
            ok = 200 <= r.status_code < 300
        except requests.RequestException as exc:
            _log(phone, draft["to_number"], draft["message"], "failed", type(exc).__name__)
            return "failed", draft["to_number"]
        _log(phone, draft["to_number"], draft["message"], "placed" if ok else "failed", f"HTTP {r.status_code}")
        return ("placed" if ok else "failed"), draft["to_number"]
    _log(phone, draft["to_number"], draft["message"], "test", "test mode: nothing was dialled")
    return "test", draft["to_number"]


def discard(phone: str) -> bool:
    with db.conn() as c:
        return c.execute("DELETE FROM call_drafts WHERE phone=?", (phone,)).rowcount > 0


def has_draft(phone: str) -> bool:
    with db.conn() as c:
        return c.execute("SELECT 1 FROM call_drafts WHERE phone=?", (phone,)).fetchone() is not None


def handle(client: dict, cmd: str) -> str | None:
    """The 'ara' / 'iptal' words after the assistant prepared a call."""
    phone, lang = client["phone"], client["lang"]
    if cmd in CALL_WORDS and has_draft(phone):
        status, number = place(phone)
        key = {"placed": "placed", "test": "test", "limit": "limit", "off": "off"}.get(status, "failed")
        return t(lang, key, to=number)
    if cmd in CANCEL_WORDS and has_draft(phone):
        discard(phone)
        return t(lang, "dropped")
    return None


def recent(limit: int = 25) -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT l.at, l.phone, l.to_number, l.status, l.detail, COALESCE(c.name,'') AS name FROM call_log l "
                         "LEFT JOIN clients c ON c.phone=l.phone ORDER BY l.id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
