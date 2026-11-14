"""Nightly copy of the database (SQLite's own online backup, safe while the bot runs), gzip-compressed, newest N kept.

The master key (MAIL_MASTER_KEY) is NOT inside the backup: mail logins in a stolen backup stay sealed.
Copy the folder off the droplet too (README: rclone / scp), a backup on the same disk is not a backup.
"""
from __future__ import annotations

import gzip
import logging
import os
import shutil
import sqlite3
from datetime import datetime

from . import config, db, ops

log = logging.getLogger(__name__)


def backup_dir() -> str:
    return config.BACKUP_DIR


def run_backup(now: datetime | None = None) -> str:
    """Write data/backups/assistant-YYYYmmdd-HHMMSS.db.gz and prune old ones. Returns the path."""
    os.makedirs(backup_dir(), exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    raw = os.path.join(backup_dir(), f"assistant-{stamp}.db")
    src = sqlite3.connect(config.DB_PATH)
    dst = sqlite3.connect(raw)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    out = raw + ".gz"
    with open(raw, "rb") as fin, gzip.open(out, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout)
    os.remove(raw)
    os.chmod(out, 0o600)
    files = list_backups()
    for old in files[config.BACKUP_KEEP:]:
        try:
            os.remove(old["path"])
        except OSError:
            pass
    return out


def list_backups() -> list[dict]:
    if not os.path.isdir(backup_dir()):
        return []
    names = sorted((n for n in os.listdir(backup_dir()) if n.startswith("assistant-") and n.endswith(".db.gz")), reverse=True)
    out = []
    for n in names:
        p = os.path.join(backup_dir(), n)
        out.append({"name": n, "path": p, "size": os.path.getsize(p)})
    return out


def run_safely() -> None:
    try:
        run_backup()
    except Exception as exc:
        log.exception("backup failed")
        ops.event("backup", f"The nightly backup failed: {type(exc).__name__}", f"backup:{db.now_utc().strftime('%Y-%m-%d')}")
