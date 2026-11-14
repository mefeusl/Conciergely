"""Timers: due reminders every 30 s, stock limit checks every few minutes, nightly clean-up."""
from __future__ import annotations

import logging
import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import alerts, backup, billing, briefing, config, db, i18n, jobs, lifecycle, news, ops, scanner, sharing, trial

log = logging.getLogger(__name__)


def tick_reminders() -> int:
    """Queue every due reminder exactly once. Returns how many were queued."""
    queued = 0
    for r in db.due_reminders(db.now_utc_iso()):
        client = db.get_client(r["phone"])
        if not client or not client["active"]:
            db.mark_reminder(r["id"], db.REMINDER_CANCELLED)
            continue
        payload = {"proactive": True}
        if r["kind"] == "before":
            payload["text"] = i18n.t(client["lang"], "reminder_before", minutes=r["lead_minutes"], text=r["text"])
        elif r["kind"] == "after":  # an unprompted check-in, so it waits out quiet hours
            payload["text"] = i18n.t(client["lang"], "reminder_followup", text=r["text"])
            payload["respect_quiet"] = True
        else:  # the reminder itself: the client chose this exact time, so it is always delivered
            payload["text"] = i18n.t(client["lang"], "reminder", text=r["text"])
        jobs.enqueue("send", r["phone"], payload, dedup_key=f"rem:{r['id']}")
        db.mark_reminder(r["id"], db.REMINDER_SENT)  # the dedup key protects against a crash right here
        if r["kind"] == "main" and r.get("shared_with"):
            sharing.deliver_copy(r)
        if r["kind"] == "main" and r.get("repeat"):
            _schedule_next(r)
        queued += 1
    return queued


def next_due(due_utc: datetime, repeat: str) -> datetime | None:
    """The next time of a repeating reminder. repeat is 'monthly:DAY' or 'yearly:DAY' (DAY = the day of month to stick to)."""
    kind, _, anchor = (repeat or "").partition(":")
    if kind not in ("monthly", "yearly"):
        return None
    local = due_utc.astimezone(ZoneInfo(config.TIMEZONE))
    nxt = billing.add_months(local, 1 if kind == "monthly" else 12)
    try:
        day = min(int(anchor), calendar.monthrange(nxt.year, nxt.month)[1])
        nxt = nxt.replace(day=day)
    except ValueError:
        pass
    return nxt.astimezone(timezone.utc)


def _schedule_next(r: dict) -> None:
    """A repeating reminder just fired: create the next one (with the same heads-up / follow-up)."""
    due = db.from_iso(r["due_at"])
    nxt = next_due(due, r["repeat"]) if due else None
    if not nxt:
        return
    new_id = db.add_reminder(r["phone"], r["text"], db.to_iso(nxt), repeat=r["repeat"], shared_with=r.get("shared_with") or "")
    for child in db.reminder_children(r["id"]):
        shift = timedelta(minutes=child["lead_minutes"])
        when = nxt - shift if child["kind"] == "before" else nxt + shift
        db.add_reminder(r["phone"], r["text"], db.to_iso(when), child["kind"], new_id, child["lead_minutes"])


def first_scan() -> None:
    """After a fresh install or a long outage, run the scans that have no recent results."""
    for market in scanner.MARKETS:
        if not scanner.scan_age_ok(market, max_hours=72):
            scanner.run_scan(market)


def _guarded(fn):
    def run():
        try:
            fn()
        except Exception:
            log.exception("scheduled task %s failed", fn.__name__)
    run.__name__ = fn.__name__
    return run


def start():
    from apscheduler.schedulers.background import BackgroundScheduler  # lazy: optional when testing

    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(_guarded(tick_reminders), "interval", seconds=30, max_instances=1, coalesce=True)
    sched.add_job(_guarded(alerts.run_cycle), "interval", minutes=max(1, config.ALERT_CHECK_MINUTES),
                  max_instances=1, coalesce=True)
    sched.add_job(_guarded(briefing.tick), "interval", minutes=1, max_instances=1, coalesce=True)
    sched.add_job(_guarded(lifecycle.tick), "cron", minute=2, timezone=config.TIMEZONE, max_instances=1, coalesce=True)
    sched.add_job(_guarded(trial.tick), "interval", minutes=30, max_instances=1, coalesce=True)
    sched.add_job(_guarded(billing.tick), "interval", minutes=30, max_instances=1, coalesce=True)
    sched.add_job(_guarded(ops.tick), "interval", minutes=5, max_instances=1, coalesce=True)
    sched.add_job(backup.run_safely, "cron", hour=3, minute=40, timezone=config.TIMEZONE)
    sched.add_job(_guarded(jobs.purge_old), "cron", hour=1, minute=17)
    sched.add_job(_guarded(news.run_cycle), "interval", minutes=20, max_instances=1, coalesce=True)
    sched.add_job(_guarded(lambda: news.prune()), "cron", hour=2, minute=11)
    # Nightly chart scans: BIST after its close, US after its close (23:00-00:00 Istanbul). Weekdays only.
    sched.add_job(_guarded(lambda: scanner.run_scan("bist")), "cron", day_of_week="mon-fri", hour=19, minute=15,
                  timezone=config.TIMEZONE, max_instances=1, coalesce=True)
    sched.add_job(_guarded(lambda: scanner.run_scan("us")), "cron", day_of_week="tue-sat", hour=5, minute=30,
                  timezone=config.TIMEZONE, max_instances=1, coalesce=True)
    sched.add_job(_guarded(first_scan), "date", run_date=db.now_utc() + timedelta(minutes=2))
    sched.start()
    return sched
