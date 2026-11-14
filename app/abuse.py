"""Spots exploitation patterns and freezes the client.

Usage itself is not limited. What is caught is a pattern: many huge prompts in an hour, or an hour that burns an
extreme amount of AI tokens. A frozen client gets one polite notice a day, nothing else is processed for them
(scheduled reminders still arrive), and you see it in /admin and can unfreeze with one click. After an unfreeze the
counters start again from that moment.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from . import config, db, jobs, settings

log = logging.getLogger(__name__)


def _since(client: dict | None) -> str:
    """Start of the one-hour window, but never earlier than the last unfreeze."""
    hour_ago = db.to_iso(db.now_utc() - timedelta(hours=1))
    reset = (client or {}).get("abuse_reset_at") or ""
    return max(hour_ago, reset)


def huge_count(client: dict) -> int:
    with db.conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE phone=? AND kind='inbound' AND created_at>? "
            "AND json_extract(payload, '$.type')='text' AND length(json_extract(payload, '$.text')) >= ?",
            (client["phone"], _since(client), config.ABUSE_HUGE_CHARS)).fetchone()
    return int(row["n"])


def log_tokens(phone: str, tokens: int) -> None:
    if tokens > 0:
        with db.conn() as c:
            c.execute("INSERT INTO token_log(phone, at, tokens) VALUES(?,?,?)", (phone, db.now_utc_iso(), int(tokens)))


def tokens_last_hour(client: dict) -> int:
    with db.conn() as c:
        row = c.execute("SELECT COALESCE(SUM(tokens),0) AS n FROM token_log WHERE phone=? AND at>?",
                        (client["phone"], _since(client))).fetchone()
    return int(row["n"])


def last_hour(client: dict) -> dict:
    with db.conn() as c:
        msgs = c.execute("SELECT COUNT(*) AS n FROM jobs WHERE phone=? AND kind='inbound' AND created_at>?",
                         (client["phone"], _since(client))).fetchone()["n"]
    return {"messages": int(msgs), "huge": huge_count(client), "tokens": tokens_last_hour(client)}


def notice_text(client: dict) -> str:
    owner = settings.contact_phone()  # empty when the owner switched the contact number off in /admin
    if client.get("lang") == "en":
        if owner:
            return ("Your chat has been paused because of suspicious activity on your account. To continue you need to "
                    f"talk to me: please call my personal number {owner} in the afternoon. If I can't pick up, I will call you back.")
        return ("Your chat has been paused because of suspicious activity on your account. To continue, please contact "
                "the person who set up your assistant.")
    if owner:
        return ("Hesabınızda şüpheli bir kullanım hareketi fark edildiği için sohbetiniz durduruldu. Devam edebilmeniz için "
                f"benimle konuşmanız gerekiyor: lütfen öğleden sonra {owner} numaralı kişisel hattımı arayın. Açamazsam ben sizi geri ararım.")
    return ("Hesabınızda şüpheli bir kullanım hareketi fark edildiği için sohbetiniz durduruldu. Devam edebilmek için lütfen "
            "asistanınızı kurulumunu yapan kişiyle iletişime geçin.")


def send_notice(client: dict, delay_seconds: int = 0) -> None:
    """One notice per client per day, however many messages they keep sending."""
    day = db.now_utc().strftime("%Y-%m-%d")
    jobs.enqueue("send", client["phone"], {"text": notice_text(client)}, dedup_key=f"frozen:{client['phone']}:{day}",
                 delay_seconds=delay_seconds)


def freeze(phone: str, reason: str) -> None:
    db.set_client_fields(phone, frozen=1, frozen_reason=reason[:200], frozen_at=db.now_utc_iso())
    log.warning("client ending %s FROZEN: %s", db.last4(phone), reason)


def unfreeze(phone: str) -> None:
    db.set_client_fields(phone, frozen=0, frozen_reason="", abuse_reset_at=db.now_utc_iso())


def check_inbound(client: dict, text_len: int) -> bool:
    """Called before a message is queued. True = this message trips the huge-prompt rule (client is now frozen)."""
    if text_len < config.ABUSE_HUGE_CHARS or config.ABUSE_HUGE_PER_HOUR <= 0:
        return False
    if huge_count(client) + 1 >= config.ABUSE_HUGE_PER_HOUR:
        freeze(client["phone"], f"{config.ABUSE_HUGE_PER_HOUR} huge prompts (over {config.ABUSE_HUGE_CHARS} characters) within an hour")
        return True
    return False


def check_tokens(phone: str) -> bool:
    """Called after a reply. True = the hour's AI usage is extreme (client is now frozen)."""
    client = db.get_client(phone)
    if not client or client["frozen"] or config.ABUSE_TOKENS_PER_HOUR <= 0:
        return False
    used = tokens_last_hour(client)
    if used >= config.ABUSE_TOKENS_PER_HOUR:
        freeze(phone, f"{used:,} AI tokens within an hour")
        send_notice(db.get_client(phone), delay_seconds=2)  # a moment later, so the answer arrives first
        return True
    return False
