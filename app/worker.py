"""Background workers: take jobs off the queue and run them."""
from __future__ import annotations

import logging
import threading

from . import briefing, config, conversation, db, i18n, jobs, lifecycle, quiet, whatsapp
from .errors import Deferred, PermanentSendError

log = logging.getLogger(__name__)


def deliver(job: dict) -> None:
    """Send one outgoing message. Reminders and alerts ('proactive') may fall outside WhatsApp's 24 h window."""
    phone, p = job["phone"], job["payload"]
    if p.get("hello"):  # admin "send test ping": Meta's built-in template always works
        if db.window_open(phone):
            whatsapp.send_text(phone, i18n.t((db.get_client(phone) or {}).get("lang", "tr"), "test_ping"))
        else:
            whatsapp.send_hello(phone)
        return
    text = p["text"]
    if p.get("direct"):  # a reply to someone who just wrote to us: the 24 h window is open even without a client row
        whatsapp.send_text(phone, text)
        return
    if p.get("respect_quiet"):  # market alerts and follow-ups wait until the client's quiet hours end
        client = db.get_client(phone)
        if client and quiet.in_quiet(client):
            raise Deferred(db.to_iso(quiet.next_end(client)))
    if p.get("button") and not (p.get("proactive") and not db.window_open(phone)):
        _deliver_with_button(phone, text, p["button"])
        return
    if p.get("proactive") and not db.window_open(phone):
        if config.WHATSAPP_PROVIDER == "meta" and config.META_TEMPLATE_NAME:
            whatsapp.send_template(phone, text)
            return
        raise PermanentSendError("24h window closed and no approved template configured (META_TEMPLATE_NAME)")
    whatsapp.send_text(phone, text)


def _deliver_with_button(phone: str, text: str, button: dict) -> None:
    """Text plus a tap button. If WhatsApp refuses the button message, send the link as plain text instead."""
    label, url = str(button.get("label", "")), str(button.get("url", ""))
    body = text
    if len(text) > 900:  # button messages have a short body; send the long text first
        whatsapp.send_text(phone, text)
        body = i18n.t((db.get_client(phone) or {}).get("lang", "tr"), "tap_below")
    try:
        whatsapp.send_link_button(phone, body, label, url)
    except PermanentSendError as exc:
        log.warning("button message refused (%s); sending the link as text", exc)
        whatsapp.send_text(phone, f"{body}\n{url}")


def handle(job: dict) -> None:
    if job["kind"] == "inbound":
        conversation.process_inbound(job)
    elif job["kind"] == "send":
        deliver(job)
    elif job["kind"] == "wow":
        lifecycle.deliver_wow(job)
    elif job["kind"] == "briefing":
        briefing.deliver(job)
    else:
        raise PermanentSendError(f"unknown job kind {job['kind']}")


def process_one() -> bool:
    """Run the next ready job. Returns False when the queue has nothing ready."""
    job = jobs.claim()
    if job is None:
        return False
    try:
        handle(job)
        jobs.complete(job["id"])
    except Deferred as wait:
        log.info("job %s held back until %s (quiet hours)", job["id"], wait.until)
        jobs.defer(job["id"], wait.until)
    except PermanentSendError as exc:
        log.error("job %s (%s) failed permanently: %s", job["id"], job["kind"], exc)
        jobs.fail(job["id"], str(exc), permanent=True)
    except Exception as exc:
        log.exception("job %s (%s) failed on attempt %s", job["id"], job["kind"], job["attempts"])
        jobs.fail(job["id"], f"{type(exc).__name__}: {exc}")
    return True


def _loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            busy = process_one()
        except Exception:  # the database itself failed; wait and keep the worker alive
            log.exception("worker loop error")
            busy = False
        if not busy:
            stop.wait(1.0)


def start(stop: threading.Event) -> list[threading.Thread]:
    revived = jobs.requeue_stale(0)  # anything still 'running' belongs to a process that no longer exists
    if revived:
        log.info("re-queued %d interrupted jobs", revived)
    threads = [threading.Thread(target=_loop, args=(stop,), name=f"worker-{n}", daemon=True)
               for n in range(max(1, config.WORKER_THREADS))]
    for t in threads:
        t.start()
    return threads
