"""SQLite storage: clients, chat history, reminders, watchlists. (The job queue lives in jobs.py.)

Every call opens its own short connection inside one write transaction (BEGIN IMMEDIATE), which is
plenty for dozens of clients and keeps the code free of locking surprises.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import config

log = logging.getLogger(__name__)

HISTORY_KEEP = 200  # chat messages kept per client (retention limit)

REMINDER_PENDING, REMINDER_SENT, REMINDER_FAILED, REMINDER_CANCELLED = 0, 1, 2, 3

CLIENT_FIELDS = {
    "name", "preferences", "active", "lang", "onboarding_step", "onboarding_done",
    "last_inbound_at", "pending_action", "quiet_start", "quiet_end", "news_alerts",
    "plan", "briefing_min", "briefing_last", "work_area", "frozen", "frozen_reason", "frozen_at", "abuse_reset_at",
    "trial_days", "trial_ends_at", "trial_notes", "calls_on",
    "paid_until", "free", "paid_months", "setup_paid_tl", "pay_code", "pay_notes", "grace_until",
    "ref_code", "referred_by", "ref_notes", "recap_on", "recap_last", "wow_sent",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  preferences TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  lang TEXT NOT NULL DEFAULT 'tr',
  onboarding_step INTEGER NOT NULL DEFAULT 0,
  onboarding_done INTEGER NOT NULL DEFAULT 0,
  last_inbound_at TEXT NOT NULL DEFAULT '',
  pending_action TEXT NOT NULL DEFAULT '',
  quiet_start INTEGER NOT NULL DEFAULT -1,
  quiet_end INTEGER NOT NULL DEFAULT -1,
  news_alerts INTEGER NOT NULL DEFAULT 0,
  plan TEXT NOT NULL DEFAULT 'basic',
  briefing_min INTEGER NOT NULL DEFAULT -1,
  briefing_last TEXT NOT NULL DEFAULT '',
  work_area TEXT NOT NULL DEFAULT '',
  frozen INTEGER NOT NULL DEFAULT 0,
  frozen_reason TEXT NOT NULL DEFAULT '',
  frozen_at TEXT NOT NULL DEFAULT '',
  abuse_reset_at TEXT NOT NULL DEFAULT '',
  trial_days INTEGER NOT NULL DEFAULT 0,
  trial_ends_at TEXT NOT NULL DEFAULT '',
  trial_notes INTEGER NOT NULL DEFAULT 0,
  calls_on INTEGER NOT NULL DEFAULT 0,
  paid_until TEXT NOT NULL DEFAULT '',
  free INTEGER NOT NULL DEFAULT 0,
  paid_months INTEGER NOT NULL DEFAULT 0,
  setup_paid_tl INTEGER NOT NULL DEFAULT 0,
  pay_code TEXT NOT NULL DEFAULT '',
  pay_notes INTEGER NOT NULL DEFAULT 0,
  grace_until TEXT NOT NULL DEFAULT '',
  ref_code TEXT NOT NULL DEFAULT '',
  referred_by TEXT NOT NULL DEFAULT '',
  ref_notes INTEGER NOT NULL DEFAULT 0,
  recap_on INTEGER NOT NULL DEFAULT 1,
  recap_last TEXT NOT NULL DEFAULT '',
  wow_sent INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS referrals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  referrer TEXT NOT NULL,
  friend TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  rewarded_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS referral_credits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  pct INTEGER NOT NULL,
  friend TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  used_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS share_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  member TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  UNIQUE(owner, member)
);
CREATE TABLE IF NOT EXISTS unknown_contacts (
  phash TEXT PRIMARY KEY,
  last_reply_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ever_clients (
  phash TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  at TEXT NOT NULL,
  amount_tl INTEGER NOT NULL DEFAULT 0,
  plan TEXT NOT NULL DEFAULT '',
  months INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'manual',
  status TEXT NOT NULL DEFAULT 'applied',
  fingerprint TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_payments_phone ON payments(phone, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_fp ON payments(fingerprint) WHERE fingerprint != '' AND status IN ('applied','review');
CREATE TABLE IF NOT EXISTS voice_links (
  token_hash TEXT PRIMARY KEY,
  phone TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  turns INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS token_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  at TEXT NOT NULL,
  tokens INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_token_log ON token_log(phone, at);
CREATE TABLE IF NOT EXISTS usage (
  phone TEXT NOT NULL,
  month TEXT NOT NULL,
  key TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (phone, month, key)
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_phone ON messages(phone, id);
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  text TEXT NOT NULL,
  due_at TEXT NOT NULL,
  status INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'main',
  parent_id INTEGER NOT NULL DEFAULT 0,
  lead_minutes INTEGER NOT NULL DEFAULT 0,
  repeat TEXT NOT NULL DEFAULT '',
  shared_with TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at);
CREATE TABLE IF NOT EXISTS watchlist (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  symbol TEXT NOT NULL,
  upper REAL,
  lower REAL,
  move_pct REAL,
  state TEXT NOT NULL DEFAULT 'unknown',
  pct_alert_date TEXT NOT NULL DEFAULT '',
  UNIQUE(phone, symbol)
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  phone TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  run_at TEXT NOT NULL,
  dedup_key TEXT UNIQUE,
  last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_ready ON jobs(status, run_at);
CREATE TABLE IF NOT EXISTS scan_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  setup TEXT NOT NULL,
  symbol TEXT NOT NULL,
  close REAL NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  scanned_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scan_market ON scan_results(market, setup);
CREATE TABLE IF NOT EXISTS setup_stats (
  market TEXT NOT NULL,
  setup TEXT NOT NULL,
  stats TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (market, setup)
);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  qty REAL NOT NULL,
  price REAL NOT NULL,
  fee REAL NOT NULL DEFAULT 0,
  traded_on TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_phone ON trades(phone, symbol);
CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL DEFAULT 'long',
  thesis TEXT NOT NULL DEFAULT '',
  confidence INTEGER NOT NULL DEFAULT 50,
  entry_price REAL,
  review_on TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  exit_price REAL,
  outcome_pct REAL,
  created_at TEXT NOT NULL,
  closed_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_journal_phone ON journal(phone, status);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mail_accounts (
  phone TEXT PRIMARY KEY,
  secret TEXT NOT NULL,
  folder TEXT NOT NULL DEFAULT 'INBOX',
  senders TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mail_drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  to_addr TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  in_reply_to TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drafts_phone ON mail_drafts(phone, status);
CREATE TABLE IF NOT EXISTS mail_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  at TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_phone ON mail_audit(phone, id);
CREATE TABLE IF NOT EXISTS call_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  at TEXT NOT NULL,
  to_number TEXT NOT NULL,
  message TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS call_drafts (
  phone TEXT PRIMARY KEY,
  to_number TEXT NOT NULL,
  message TEXT NOT NULL,
  lang TEXT NOT NULL DEFAULT 'tr',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ops_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  dedup_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS upgrade_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  at TEXT NOT NULL,
  handled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS news_seen (
  phone TEXT NOT NULL,
  key TEXT NOT NULL,
  seen_at TEXT NOT NULL,
  sent INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (phone, key)
);
"""


# ---------- time helpers ----------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def now_utc_iso() -> str:
    return to_iso(now_utc())


def from_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------- phone numbers ----------

PHONE_RE = re.compile(r"^\+\d{8,15}$")


def norm_phone(raw: str) -> str:
    """Normalise 'whatsapp:+90 555 123 45 67', '0555 123 45 67', '905551234567' -> '+905551234567'.

    Numbers without a country code are assumed Turkish. Always enter foreign numbers with '+'.
    """
    digits = re.sub(r"[^\d+]", "", (raw or "").replace("whatsapp:", ""))
    if not digits:
        return ""
    if digits.startswith("+"):
        return "+" + digits[1:].replace("+", "")
    digits = digits.replace("+", "")
    if digits.startswith("00"):
        return "+" + digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        return "+9" + digits
    if len(digits) == 10:
        return "+90" + digits
    return "+" + digits


def valid_phone(phone: str) -> bool:
    return bool(PHONE_RE.match(phone or ""))


def last4(phone: str) -> str:
    return (phone or "")[-4:]


# ---------- connections ----------

def _connect() -> sqlite3.Connection:
    parent = os.path.dirname(config.DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c


@contextmanager
def conn():
    """One write transaction: commits on success, rolls back on any error."""
    c = _connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        c.execute("COMMIT")
    finally:
        c.close()


# Columns added after the first release. Existing databases get them here (new ones have them in SCHEMA).
_ADDED_COLUMNS = {
    "clients": [("quiet_start", "INTEGER NOT NULL DEFAULT -1"), ("quiet_end", "INTEGER NOT NULL DEFAULT -1"),
                ("news_alerts", "INTEGER NOT NULL DEFAULT 0"), ("plan", "TEXT NOT NULL DEFAULT 'basic'"),
                ("briefing_min", "INTEGER NOT NULL DEFAULT -1"), ("briefing_last", "TEXT NOT NULL DEFAULT ''"),
                ("work_area", "TEXT NOT NULL DEFAULT ''"), ("frozen", "INTEGER NOT NULL DEFAULT 0"),
                ("frozen_reason", "TEXT NOT NULL DEFAULT ''"), ("frozen_at", "TEXT NOT NULL DEFAULT ''"),
                ("abuse_reset_at", "TEXT NOT NULL DEFAULT ''"),
                ("trial_days", "INTEGER NOT NULL DEFAULT 0"), ("trial_ends_at", "TEXT NOT NULL DEFAULT ''"),
                ("trial_notes", "INTEGER NOT NULL DEFAULT 0"), ("calls_on", "INTEGER NOT NULL DEFAULT 0"),
                ("paid_until", "TEXT NOT NULL DEFAULT ''"), ("free", "INTEGER NOT NULL DEFAULT 0"),
                ("paid_months", "INTEGER NOT NULL DEFAULT 0"), ("setup_paid_tl", "INTEGER NOT NULL DEFAULT 0"),
                ("pay_code", "TEXT NOT NULL DEFAULT ''"), ("pay_notes", "INTEGER NOT NULL DEFAULT 0"),
                ("grace_until", "TEXT NOT NULL DEFAULT ''"),
                ("ref_code", "TEXT NOT NULL DEFAULT ''"), ("referred_by", "TEXT NOT NULL DEFAULT ''"),
                ("ref_notes", "INTEGER NOT NULL DEFAULT 0"), ("recap_on", "INTEGER NOT NULL DEFAULT 1"),
                ("recap_last", "TEXT NOT NULL DEFAULT ''"), ("wow_sent", "INTEGER NOT NULL DEFAULT 0")],
    "reminders": [("kind", "TEXT NOT NULL DEFAULT 'main'"), ("parent_id", "INTEGER NOT NULL DEFAULT 0"),
                  ("lead_minutes", "INTEGER NOT NULL DEFAULT 0"), ("repeat", "TEXT NOT NULL DEFAULT ''"),
                  ("shared_with", "TEXT NOT NULL DEFAULT ''")],
}


def _migrate(c: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():  # fixed names, never user input
        have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns:
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                if (table, name) == ("clients", "plan"):  # clients that existed before plans keep every feature
                    c.execute("UPDATE clients SET plan='plus'")


def init_db() -> None:
    c = _connect()
    try:
        c.executescript(SCHEMA)
        _migrate(c)
        for row in c.execute("SELECT phone FROM clients").fetchall():  # clients from before this table existed
            c.execute("INSERT OR IGNORE INTO ever_clients(phash) VALUES(?)", (phash(row["phone"]),))
        _encrypt_old_reminders(c)
    finally:
        c.close()


# ---------- clients ----------

def get_client(phone: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM clients WHERE phone=?", (norm_phone(phone),)).fetchone()
    return dict(row) if row else None


def get_client_by_id(client_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    return dict(row) if row else None


def phash(phone: str) -> str:
    """One-way fingerprint of a number: lets us remember 'was a client once' / 'was told about us' without keeping the number."""
    import hashlib
    import hmac
    key = config.PHASH_SALT.encode()
    return hmac.new(key, norm_phone(phone).encode(), hashlib.sha256).hexdigest()


def upsert_client(phone: str, name: str = "", preferences: str = "", active: int = 1) -> dict:
    phone = norm_phone(phone)
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO ever_clients(phash) VALUES(?)", (phash(phone),))
    with conn() as c:
        c.execute(
            """INSERT INTO clients(phone, name, preferences, active, created_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(phone) DO UPDATE SET
                 name=excluded.name, preferences=excluded.preferences, active=excluded.active""",
            (phone, name, preferences, int(active), now_utc_iso()),
        )
    return get_client(phone)  # type: ignore[return-value]


def set_client_fields(phone: str, **fields) -> None:
    bad = set(fields) - CLIENT_FIELDS
    if bad:
        raise ValueError(f"unknown client fields: {sorted(bad)}")
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)  # keys are whitelisted above
    with conn() as c:
        c.execute(f"UPDATE clients SET {cols} WHERE phone=?", (*fields.values(), norm_phone(phone)))


def list_clients() -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM clients ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def erase_client(phone: str) -> None:
    """Remove a client and ALL their data: DB rows, queued jobs and the memory file."""
    from . import memory  # local import keeps db.py free of file-system code at import time

    phone = norm_phone(phone)
    with conn() as c:
        for table in ("messages", "reminders", "watchlist", "jobs", "trades", "journal", "news_seen", "usage", "token_log", "mail_accounts", "mail_drafts", "mail_audit", "call_log", "call_drafts", "upgrade_requests", "payments", "voice_links", "referral_credits", "clients"):
            c.execute(f"DELETE FROM {table} WHERE phone=?", (phone,))  # fixed table names
        c.execute("DELETE FROM referrals WHERE friend=? OR referrer=?", (phone, phone))
        c.execute("DELETE FROM share_links WHERE owner=? OR member=?", (phone, phone))
        c.execute("UPDATE reminders SET shared_with='' WHERE shared_with=?", (phone,))
    memory.forget_all(phone)


# ---------- chat history ----------

def add_message(phone: str, role: str, content: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO messages(phone, role, content, created_at) VALUES(?,?,?,?)",
            (phone, role, content, now_utc_iso()),
        )
        c.execute(
            """DELETE FROM messages WHERE phone=? AND id NOT IN
               (SELECT id FROM messages WHERE phone=? ORDER BY id DESC LIMIT ?)""",
            (phone, phone, HISTORY_KEEP),
        )


def hours_since_last_message(phone: str, now: datetime | None = None) -> float | None:
    """Hours since the client's previous message (None if there is none). Used so the model knows a new message is a fresh start."""
    with conn() as c:
        row = c.execute("SELECT created_at FROM messages WHERE phone=? AND role='user' ORDER BY id DESC LIMIT 1", (phone,)).fetchone()
    last = from_iso(row["created_at"]) if row else None
    if not last:
        return None
    return max(0.0, ((now or now_utc()) - last).total_seconds() / 3600)


def get_history(phone: str, turns: int | None = None) -> list[dict]:
    """Last N turns as Claude-ready messages: alternating roles, starting with 'user'."""
    turns = turns or config.HISTORY_TURNS
    with conn() as c:
        rows = c.execute(
            "SELECT role, content FROM messages WHERE phone=? ORDER BY id DESC LIMIT ?",
            (phone, turns * 2),
        ).fetchall()
    msgs: list[dict] = []
    for r in reversed(rows):
        if msgs and msgs[-1]["role"] == r["role"]:
            msgs[-1]["content"] += "\n" + r["content"]
        else:
            msgs.append({"role": r["role"], "content": r["content"]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    while msgs and msgs[-1]["role"] != "assistant":
        msgs.pop()
    return msgs


# ---------- reminders ----------

_ENC = "enc:"  # reminder texts are stored sealed (AES-256-GCM, vault.py) when MAIL_MASTER_KEY is set


def _seal_reminder(phone: str, text: str) -> str:
    from . import vault  # local import: vault needs only config, but keep db.py light at import time

    if text.startswith(_ENC) or not vault.available():
        return text
    try:
        return _ENC + vault.seal(f"reminder:{phone}", text)
    except vault.VaultError:
        return text


def _open_reminder(row: dict) -> dict:
    """The row with its text decrypted. A text that cannot be opened is replaced, never raised: the scheduler must keep running."""
    text = row.get("text") or ""
    if text.startswith(_ENC):
        from . import vault

        try:
            row = {**row, "text": vault.open_(f"reminder:{row['phone']}", text[len(_ENC):])}
        except vault.VaultError:
            log.error("a reminder text could not be decrypted (id %s): is MAIL_MASTER_KEY(_OLD) correct?", row.get("id"))
            row = {**row, "text": "(reminder text unavailable)"}
    return row


def _encrypt_old_reminders(c) -> int:
    """Seal reminder texts saved before encryption existed. Safe to run on every start."""
    from . import vault

    if not vault.available():
        return 0
    rows = c.execute("SELECT id, phone, text FROM reminders WHERE text NOT LIKE ?", (_ENC + "%",)).fetchall()
    for r in rows:
        c.execute("UPDATE reminders SET text=? WHERE id=?", (_seal_reminder(r["phone"], r["text"]), r["id"]))
    c.commit()
    return len(rows)


def add_reminder(phone: str, text: str, due_at_utc: str, kind: str = "main", parent_id: int = 0,
                 lead_minutes: int = 0, repeat: str = "", shared_with: str = "") -> int:
    """kind: 'main' (the reminder itself), 'before' (heads-up) or 'after' (follow-up check). The text is stored encrypted."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO reminders(phone, text, due_at, kind, parent_id, lead_minutes, repeat, shared_with, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (phone, _seal_reminder(phone, text), due_at_utc, kind, parent_id, lead_minutes, repeat, shared_with, now_utc_iso()),
        )
        return int(cur.lastrowid)


def list_pending_reminders(phone: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM reminders WHERE phone=? AND status=? ORDER BY due_at",
            (phone, REMINDER_PENDING),
        ).fetchall()
    return [_open_reminder(dict(r)) for r in rows]


def cancel_reminder(phone: str, reminder_id: int) -> bool:
    """Cancel a pending reminder together with its heads-up and follow-up."""
    with conn() as c:
        cur = c.execute(
            "UPDATE reminders SET status=? WHERE id=? AND phone=? AND status=?",
            (REMINDER_CANCELLED, reminder_id, phone, REMINDER_PENDING),
        )
        if cur.rowcount > 0:
            c.execute(
                "UPDATE reminders SET status=? WHERE parent_id=? AND phone=? AND status=?",
                (REMINDER_CANCELLED, reminder_id, phone, REMINDER_PENDING),
            )
        return cur.rowcount > 0


def due_reminders(now_utc_str: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM reminders WHERE status=? AND due_at<=? ORDER BY due_at",
            (REMINDER_PENDING, now_utc_str),
        ).fetchall()
    return [_open_reminder(dict(r)) for r in rows]


def reminder_children(parent_id: int) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT kind, lead_minutes FROM reminders WHERE parent_id=? AND kind IN ('before','after')", (parent_id,)).fetchall()
    return [dict(r) for r in rows]


def mark_reminder(reminder_id: int, status: int) -> None:
    with conn() as c:
        c.execute("UPDATE reminders SET status=? WHERE id=?", (status, reminder_id))


# ---------- watchlist ----------

def add_watch(phone: str, symbol: str, upper: float | None, lower: float | None,
              move_pct: float | None) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO watchlist(phone, symbol, upper, lower, move_pct) VALUES(?,?,?,?,?)
               ON CONFLICT(phone, symbol) DO UPDATE SET
                 upper=excluded.upper, lower=excluded.lower, move_pct=excluded.move_pct,
                 state='unknown', pct_alert_date=''""",
            (phone, symbol, upper, lower, move_pct),
        )


def remove_watch(phone: str, symbol: str) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM watchlist WHERE phone=? AND symbol=?", (phone, symbol))
        return cur.rowcount > 0


def list_watch(phone: str) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM watchlist WHERE phone=? ORDER BY symbol", (phone,)).fetchall()
    return [dict(r) for r in rows]


def all_active_watches() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT w.*, c.lang AS lang FROM watchlist w
               JOIN clients c ON c.phone = w.phone WHERE c.active = 1"""
        ).fetchall()
    return [dict(r) for r in rows]


def update_watch_state(watch_id: int, state: str, pct_alert_date: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE watchlist SET state=?, pct_alert_date=? WHERE id=?",
            (state, pct_alert_date, watch_id),
        )


def window_open(phone: str, hours: float = 23.0) -> bool:
    """True if the client wrote to us recently enough for free-form WhatsApp replies."""
    client = get_client(phone)
    last = from_iso(client["last_inbound_at"]) if client else None
    return bool(last and now_utc() - last < timedelta(hours=hours))
