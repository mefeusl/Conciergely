"""The free Plus trial: starts at a new client's first message, warns one day before the end, says goodbye kindly.

While it runs the client is effectively on Plus (plans.plan_of). When it ends they fall back to the plan they
pay for. Two proactive messages only: one day before, and when it ends. Both carry a short pitch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import billing, config, db, i18n, jobs, plans, referrals, settings

HEADS_UP, ENDED = 1, 2  # bits in clients.trial_notes


def contact(lang: str) -> str:
    owner = settings.contact_phone()
    if lang == "en":
        return f"reach me on {owner}" if owner else "just write to me here"
    return f"{owner} numarasından bana ulaşın" if owner else "bana buradan yazmanız yeterli"


def start_if_pending(client: dict) -> None:
    """Called when a client writes: the trial the owner granted starts now, once."""
    if client.get("trial_days", 0) > 0 and not client.get("trial_ends_at"):
        ends = db.now_utc() + timedelta(days=client["trial_days"])
        db.set_client_fields(client["phone"], trial_ends_at=db.to_iso(ends), trial_days=0, trial_notes=0)


def end_date(client: dict) -> str:
    ends = db.from_iso(client.get("trial_ends_at") or "")
    return ends.astimezone(ZoneInfo(config.TIMEZONE)).strftime("%d.%m.%Y") if ends else ""


def started_text(client: dict) -> str:
    return i18n.t(client["lang"], "trial_started", date=end_date(client), days=config.TRIAL_DAYS)


def tick(now: datetime | None = None) -> int:
    """Queue the one-day heads-up and the end notice. Returns how many messages were queued."""
    now = now or datetime.now(timezone.utc)
    queued = 0
    for c in db.list_clients():
        ends = db.from_iso(c["trial_ends_at"]) if c["trial_ends_at"] else None
        if not ends or not c["active"] or c["frozen"] or plans.raw_plan(c) == "plus" or c.get("free") or c.get("paid_until"):
            continue
        notes, kind = c["trial_notes"], None
        if now >= ends and not notes & ENDED:
            kind, notes = "trial_ended", notes | ENDED | HEADS_UP
        elif now < ends and now >= ends - timedelta(days=1) and not notes & HEADS_UP:
            kind, notes = "trial_one_day", notes | HEADS_UP
        if kind:
            db.set_client_fields(c["phone"], trial_notes=notes)
            if config.BILLING_ON and billing.configured():  # with billing on, the trial messages carry the payment details
                text = i18n.t(c["lang"], kind + "_pay", howto=billing.how_to_pay(c), days=config.FREEZE_DAYS_AFTER) \
                    if kind == "trial_ended" else i18n.t(c["lang"], kind + "_pay", howto=billing.how_to_pay(c))
            else:
                text = i18n.t(c["lang"], kind, contact=contact(c["lang"]))
            if kind == "trial_one_day":
                text += referrals.mention(c, referrals.TRIAL_BIT)
            if jobs.enqueue("send", c["phone"], {"text": text, "proactive": True, "respect_quiet": True},
                            dedup_key=f"{kind}:{c['phone']}:{c['trial_ends_at']}") is not None:
                queued += 1
    return queued
