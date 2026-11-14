"""Things that need the owner's attention: dead jobs, a stuck queue, upgrade requests.

Events are listed in /admin. If an alert number is set (settings.alert_phone) the same text is also sent there on WhatsApp.
The same event is not repeated within its window (dedup_key).
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from . import db, jobs, settings

log = logging.getLogger(__name__)
_PHONE = re.compile(r"\+?\d{7,}")
STUCK_MINUTES = 10


def event(kind: str, text: str, dedup_key: str | None = None, notify: bool = True) -> bool:
    with db.conn() as c:
        try:
            c.execute("INSERT INTO ops_events(at, kind, text, dedup_key) VALUES(?,?,?,?)",
                      (db.now_utc_iso(), kind, text[:300], dedup_key))
        except Exception:  # same dedup_key already used
            return False
        c.execute("DELETE FROM ops_events WHERE id NOT IN (SELECT id FROM ops_events ORDER BY id DESC LIMIT 200)")
    target = settings.alert_phone()
    if notify and target:
        jobs.enqueue("send", target, {"text": "⚠️ " + text[:300], "proactive": True}, dedup_key=f"ops:{dedup_key}" if dedup_key else None)
    log.warning("ops event [%s] %s", kind, _PHONE.sub(lambda m: "..." + m.group()[-4:], text))  # no full numbers in logs
    return True


def recent(limit: int = 20) -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT at, kind, text FROM ops_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def problems() -> list[str]:
    """What is wrong right now (used by /health and the admin banner)."""
    out = []
    now = db.now_utc()
    cutoff = db.to_iso(now - timedelta(minutes=STUCK_MINUTES))
    with db.conn() as c:
        stuck = c.execute("SELECT COUNT(*) AS n FROM jobs WHERE status='pending' AND run_at<=? AND kind IN ('inbound','send')",
                          (cutoff,)).fetchone()["n"]
        dead = c.execute("SELECT COUNT(*) AS n FROM jobs WHERE status='dead'").fetchone()["n"]
    if stuck:
        out.append(f"{stuck} job(s) have been waiting more than {STUCK_MINUTES} minutes")
    if dead:
        out.append(f"{dead} job(s) failed for good (see Failed messages below)")
    return out


def tick() -> int:
    """Run every few minutes: raise an event for new trouble, at most once per hour per kind."""
    hour = db.now_utc().strftime("%Y-%m-%dT%H")
    n = 0
    now = db.now_utc()
    cutoff = db.to_iso(now - timedelta(minutes=STUCK_MINUTES))
    with db.conn() as c:
        stuck = c.execute("SELECT COUNT(*) AS n FROM jobs WHERE status='pending' AND run_at<=? AND kind IN ('inbound','send')",
                          (cutoff,)).fetchone()["n"]
        dead = c.execute("SELECT COUNT(*) AS n FROM jobs WHERE status='dead' AND updated_at>?",
                         (db.to_iso(now - timedelta(hours=1)),)).fetchone()["n"]
    if stuck and event("stuck", f"The message queue is stuck: {stuck} job(s) waiting over {STUCK_MINUTES} minutes.", f"stuck:{hour}"):
        n += 1
    if dead and event("dead", f"{dead} job(s) failed permanently in the last hour. See the dashboard.", f"dead:{hour}"):
        n += 1
    return n
