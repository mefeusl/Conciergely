"""Durable job queue on SQLite.

Guarantees:
  * Jobs live on disk, so a restart loses nothing (running jobs are put back in line on startup).
  * A repeated dedup_key is ignored (Meta sometimes delivers the same message twice).
  * Inbound messages of ONE client are processed strictly in order; different clients run in parallel.
  * Failures retry after 10 s, 1 min, 5 min, 15 min; the 5th failure moves the job to 'dead'.
    A permanent failure goes to 'dead' immediately. Dead jobs can be retried from /admin.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

from . import db

BACKOFF_SECONDS = [10, 60, 300, 900]
MAX_ATTEMPTS = 5
STATUSES = ("pending", "running", "done", "dead")

_CLAIM_SQL = """
SELECT j.* FROM jobs j
WHERE j.status = 'pending' AND j.run_at <= ?
  AND NOT EXISTS (
        SELECT 1 FROM jobs k
        WHERE j.kind = 'inbound' AND k.kind = 'inbound' AND k.phone = j.phone
          AND k.id < j.id AND k.status IN ('pending', 'running'))
ORDER BY j.id LIMIT 1
"""


def enqueue(kind: str, phone: str, payload: dict, dedup_key: str | None = None,
            delay_seconds: int = 0) -> int | None:
    """Add a job. Returns its id, or None if dedup_key was already used."""
    now = db.now_utc()
    run_at = db.to_iso(now + timedelta(seconds=delay_seconds))
    with db.conn() as c:
        try:
            cur = c.execute(
                """INSERT INTO jobs(kind, phone, payload, run_at, dedup_key, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (kind, phone, json.dumps(payload, ensure_ascii=False), run_at, dedup_key,
                 db.to_iso(now), db.to_iso(now)),
            )
        except sqlite3.IntegrityError:
            return None
        return int(cur.lastrowid)


def scrub(job_id: int) -> None:
    """Blank the text of a queued inbound message (it held a password)."""
    with db.conn() as c:
        row = c.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return
        data = json.loads(row["payload"])
        data["text"] = "[removed]"
        c.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(data, ensure_ascii=False), job_id))


def claim() -> dict | None:
    """Atomically take the next ready job and mark it running (attempt counter +1)."""
    now = db.now_utc_iso()
    with db.conn() as c:
        row = c.execute(_CLAIM_SQL, (now,)).fetchone()
        if not row:
            return None
        c.execute(
            "UPDATE jobs SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
            (now, row["id"]),
        )
        job = dict(row)
    job["attempts"] += 1
    job["payload"] = json.loads(job["payload"])
    return job


def complete(job_id: int) -> None:
    with db.conn() as c:
        c.execute("UPDATE jobs SET status='done', updated_at=? WHERE id=?", (db.now_utc_iso(), job_id))


def fail(job_id: int, error: str, permanent: bool = False) -> str:
    """Record a failure. Returns the new status ('pending' for a retry, or 'dead')."""
    now = db.now_utc()
    with db.conn() as c:
        row = c.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return "dead"
        attempts = int(row["attempts"])
        if permanent or attempts >= MAX_ATTEMPTS:
            status, run_at = "dead", db.to_iso(now)
        else:
            delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
            status, run_at = "pending", db.to_iso(now + timedelta(seconds=delay))
        c.execute(
            "UPDATE jobs SET status=?, run_at=?, last_error=?, updated_at=? WHERE id=?",
            (status, run_at, (error or "")[:500], db.to_iso(now), job_id),
        )
    return status


def defer(job_id: int, run_at: str) -> None:
    """Put a running job back in line for later without counting it as a failed attempt."""
    with db.conn() as c:
        c.execute(
            "UPDATE jobs SET status='pending', run_at=?, attempts=MAX(attempts-1, 0), updated_at=? WHERE id=?",
            (run_at, db.now_utc_iso(), job_id),
        )


def requeue_stale(older_than_seconds: int = 300) -> int:
    """Put jobs stuck in 'running' (the process died mid-job) back in line."""
    cutoff = db.to_iso(db.now_utc() - timedelta(seconds=older_than_seconds))
    with db.conn() as c:
        cur = c.execute(
            "UPDATE jobs SET status='pending', updated_at=? WHERE status='running' AND updated_at<=?",
            (db.now_utc_iso(), cutoff),
        )
        return cur.rowcount


def counts() -> dict[str, int]:
    with db.conn() as c:
        rows = c.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
    out = {s: 0 for s in STATUSES}
    out.update({r["status"]: r["n"] for r in rows})
    return out


def list_jobs(status: str, limit: int = 50) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, kind, phone, status, attempts, last_error, updated_at FROM jobs "
            "WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def retry_dead(job_id: int) -> bool:
    now = db.now_utc_iso()
    with db.conn() as c:
        cur = c.execute(
            "UPDATE jobs SET status='pending', attempts=0, run_at=?, updated_at=? "
            "WHERE id=? AND status='dead'",
            (now, now, job_id),
        )
        return cur.rowcount > 0


def recent_count(phone: str, kind: str, minutes: int = 60) -> int:
    since = db.to_iso(db.now_utc() - timedelta(minutes=minutes))
    with db.conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE phone=? AND kind=? AND created_at>=?",
            (phone, kind, since),
        ).fetchone()
    return int(row["n"])


def recent_files(phone: str, minutes: int = 60) -> int:
    """Photos and documents this client sent recently (they are the expensive messages)."""
    since = db.to_iso(db.now_utc() - timedelta(minutes=minutes))
    with db.conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE phone=? AND kind='inbound' AND created_at>=? "
            "AND json_extract(payload, '$.type') IN ('image', 'document')",
            (phone, since),
        ).fetchone()
    return int(row["n"])


def purge_old(days: int = 7) -> int:
    """Delete finished jobs older than N days (keeps the dedup window for a week)."""
    cutoff = db.to_iso(db.now_utc() - timedelta(days=days))
    with db.conn() as c:
        cur = c.execute("DELETE FROM jobs WHERE status='done' AND updated_at<?", (cutoff,))
        return cur.rowcount
