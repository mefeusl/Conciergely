"""Small switches the owner changes in /admin without editing .env or restarting.

Stored in the `settings` table; a missing row falls back to the .env value (or a safe default).
  contact_phone_on  show the owner's number to clients (frozen notice, trial messages). Default: on only if OWNER_PHONE is set.
  contact_phone     the number shown. Default: OWNER_PHONE.
  alert_phone       private number that gets a WhatsApp message when something breaks. Blank = alerts only appear in /admin.
  calling_on        master switch for the calling add-on. Default: OFF.
  calling_provider  'test' (never dials, just logs) or 'webhook'.
  calling_url / calling_key (encrypted)  where the webhook provider sends call requests.
"""
from __future__ import annotations

from . import config, db


def get(key: str, default: str = "") -> str:
    with db.conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def put(key: str, value: str) -> None:
    with db.conn() as c:
        c.execute("INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                  (key, value, db.now_utc_iso()))


def flag(key: str, default: bool = False) -> bool:
    raw = get(key, "")
    return default if raw == "" else raw == "1"


def set_flag(key: str, on: bool) -> None:
    put(key, "1" if on else "0")


# ---- named accessors ----

def contact_phone() -> str:
    """The number clients are told to reach the owner on, or '' when that is switched off."""
    number = get("contact_phone", config.OWNER_PHONE).strip()
    return number if number and flag("contact_phone_on", bool(config.OWNER_PHONE)) else ""


def alert_phone() -> str:
    return get("alert_phone", "").strip()


def calling_on() -> bool:
    return flag("calling_on", False)
