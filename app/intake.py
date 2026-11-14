"""Webhook intake: decide which incoming messages to queue. Pure logic, no web framework needed."""
from __future__ import annotations

import logging

from . import abuse, billing, config, db, jobs, plans, referrals, trial
from .models import Incoming

log = logging.getLogger(__name__)

MAX_TEXT = 4000


def intake(incoming: list[Incoming]) -> int:
    """Queue messages from registered, active clients only. Returns how many jobs were queued."""
    queued = 0
    for m in incoming:
        client = db.get_client(m.phone)
        if not client:  # a stranger: reply once, or sign them up if they brought a valid referral code
            client, status = referrals.try_signup(m.phone, m.text if m.type == "text" else "")
            if client is None:
                referrals.stranger_reply(m.phone, status)
                log.info("stranger ending %s: %s", db.last4(m.phone), status)
                continue
        elif not client["active"]:
            log.info("ignored message from a deactivated number ending %s", db.last4(m.phone))
            continue
        if client["frozen"] and billing.is_unpaid_freeze(client):  # unpaid: only a receipt gets through
            if m.type in ("image", "document") and jobs.recent_files(m.phone) < config.MAX_FILES_PER_HOUR:
                db.set_client_fields(m.phone, last_inbound_at=db.now_utc_iso())
                payload = {"id": m.id, "type": m.type, "text": m.text[:MAX_TEXT], "media_id": m.media_id, "mime": m.mime,
                           "filename": m.filename[:120], "forwarded": m.forwarded}
                if jobs.enqueue("inbound", m.phone, payload, dedup_key=f"in:{m.id}") is not None:
                    queued += 1
            else:
                hour = db.now_utc().strftime("%Y%m%d%H")
                jobs.enqueue("send", m.phone, {"text": billing.pay_first_text(client)}, dedup_key=f"payfirst:{m.phone}:{hour}")
            continue
        if client["frozen"]:  # suspicious activity: nothing is processed until the owner unfreezes
            abuse.send_notice(client)
            continue
        trial.start_if_pending(client)  # the free Plus trial the owner granted starts with the first message
        if m.type == "text" and abuse.check_inbound(client, len(m.text)):
            abuse.send_notice(db.get_client(m.phone))
            continue
        if jobs.recent_count(m.phone, "inbound", 60) >= config.MAX_INBOUND_PER_HOUR:
            log.warning("rate limit: dropped message from client ending %s", db.last4(m.phone))
            continue
        if m.type in ("image", "document") and jobs.recent_files(m.phone) >= config.MAX_FILES_PER_HOUR:
            log.warning("file limit: dropped a %s from client ending %s", m.type, db.last4(m.phone))
            continue
        # Record the contact time at once: it opens WhatsApp's 24 h free-reply window.
        db.set_client_fields(m.phone, last_inbound_at=db.now_utc_iso())
        payload = {"id": m.id, "type": m.type, "text": m.text[:MAX_TEXT], "media_id": m.media_id, "mime": m.mime,
                   "filename": m.filename[:120], "forwarded": m.forwarded}
        if m.type == "location":
            payload["lat"], payload["lng"] = m.lat, m.lng
        if jobs.enqueue("inbound", m.phone, payload, dedup_key=f"in:{m.id}") is not None:
            plans.record(m.phone, "msgs")
            queued += 1
    return queued
