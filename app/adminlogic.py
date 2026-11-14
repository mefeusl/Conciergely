"""What the admin page does, as plain functions (the FastAPI routes in admin.py only call these)."""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from . import abuse, backup, billing, calls, config, costs, db, i18n, jobs, mail, memory, ops, plans, quiet, referrals, settings, receipt, trial, upgrade, vault


def funnel(clients: list[dict]) -> dict:
    """How the free trial is doing: started, running, ended on Basic, and converted to Plus."""
    started = [c for c in clients if c["trial_ends_at"]]
    running = [c for c in started if plans.trial_active(c) and plans.raw_plan(c) != "plus"]
    converted = [c for c in started if plans.raw_plan(c) == "plus"]
    ended = [c for c in started if plans.raw_plan(c) != "plus" and not plans.trial_active(c)]
    waiting = [c for c in clients if c["trial_days"] and not c["trial_ends_at"]]
    rate = round(100 * len(converted) / len(started)) if started else None
    return {"waiting": len(waiting), "started": len(started), "running": len(running), "ended": len(ended),
            "converted": len(converted), "rate": rate}


def dashboard() -> dict:
    clients = db.list_clients()
    for c in clients:
        c["facts"] = memory.count(c["phone"])
        c["watches"] = len(db.list_watch(c["phone"]))
        c["total_q"] = i18n.total_for(plans.plan_of(c))
        use = plans.usage_table(c["phone"])
        c["msgs"], c["tokens"] = use.get("msgs", 0), use.get("tokens_in", 0) + use.get("tokens_out", 0)
        c["effective"] = plans.plan_of(c)
        c["cost"] = costs.estimate(use)
        c["mail_on"] = mail.is_connected(c["phone"])
    cost_total = sum(c["cost"]["total"] for c in clients)
    return {"counts": jobs.counts(), "dead": jobs.list_jobs("dead"), "clients": clients,
            "cost_total": costs.money(cost_total), "cost_total_tl": costs.lira(cost_total),
            "frozen": [c for c in clients if c["frozen"]], "funnel": funnel(clients),
            "upgrades": upgrade.pending(), "reviews": billing.review_queue(), "events": ops.recent(8), "problems": ops.problems(),
            "total_q": i18n.TOTAL_QUESTIONS, "calling_on": settings.calling_on()}


def settings_page() -> dict:
    return {"contact_on": bool(settings.contact_phone()), "contact_phone": settings.get("contact_phone", config.OWNER_PHONE),
            "contact_flag": settings.flag("contact_phone_on", bool(config.OWNER_PHONE)),
            "alert_phone": settings.alert_phone(), "calling_on": settings.calling_on(),
            "provider": settings.get("calling_provider", "test"), "calling_url": settings.get("calling_url", ""),
            "calling_key_set": bool(settings.get("calling_key", "")), "vault_ok": vault.available(),
            "key_id": vault.current_key_id(), "backups": backup.list_backups()[:10], "backup_dir": config.BACKUP_DIR,
            "calls": calls.recent(10), "calls_per_day": config.CALLS_PER_DAY,
            "mail_accounts": _mail_count(), "mail_relay": mail.relay_url().split("//")[-1] if mail.relay_url() else "", "events": ops.recent(20), "trial_days": config.TRIAL_DAYS,
            "billing": {"on": config.BILLING_ON, "bank_ok": billing.configured(), "holder": config.BANK_HOLDER,
                        "iban": billing.iban_pretty(config.BANK_IBAN), "prices": [
                            ("Basic", config.SETUP_BASIC, config.PRICE_BASIC_MONTH), ("Plus", config.SETUP_PLUS, config.PRICE_PLUS_MONTH)],
                        "half_months": config.HALF_PRICE_MONTHS, "half_percent": config.HALF_PRICE_PERCENT}}


def _mail_count() -> int:
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM mail_accounts").fetchone()["n"]


def save_contact(on: bool, number: str, alert: str) -> tuple[bool, str]:
    raw_number, raw_alert = number.strip(), alert.strip()
    number, alert = db.norm_phone(raw_number), db.norm_phone(raw_alert)
    if (raw_number and not db.valid_phone(number)) or (raw_alert and not db.valid_phone(alert)):
        return False, "Phone numbers need the country code, for example +90 555 123 45 67."
    if on and not number:
        return False, "Type your number before switching it on."
    settings.put("contact_phone", number)
    settings.set_flag("contact_phone_on", on)
    settings.put("alert_phone", alert)
    return True, "Saved."


def save_calling(on: bool, provider: str, url: str, key: str) -> tuple[bool, str]:
    if provider not in ("test", "webhook"):
        return False, "Unknown calling provider."
    url = url.strip()
    if provider == "webhook" and url and not url.startswith("https://"):
        return False, "The webhook address must start with https://"
    if key.strip():
        if not vault.available():
            return False, "Set MAIL_MASTER_KEY in .env first; the calling key is stored sealed with it."
        settings.put("calling_key", vault.seal("settings", key.strip()))
    if on and provider == "webhook" and not (url and settings.get("calling_key", "")):
        return False, "The webhook provider needs an address and a key before calling can be switched on."
    settings.put("calling_provider", provider)
    settings.put("calling_url", url)
    settings.set_flag("calling_on", on)
    return True, "Saved."


def set_calls(client_id: int, on: bool) -> bool:
    c = db.get_client_by_id(client_id)
    if not c:
        return False
    db.set_client_fields(c["phone"], calls_on=1 if on else 0)
    return True


def connect_mail(client_id: int, address: str, password: str, folder: str, senders: str) -> tuple[bool, str]:
    c = db.get_client_by_id(client_id)
    if not c:
        return False, "Client not found."
    try:
        mail.connect(c["phone"], address, password, folder.strip() or "INBOX", senders.strip())
    except mail.MailError as exc:
        return False, {"no_key": "Set MAIL_MASTER_KEY in .env first (see README).",
                       "bad_input": "Check the address and password.",
                       "login_failed": "The mail server refused the login. Use an app password and check 2-step verification.",
                       "smtp_failed": "Reading works, but the sending login failed.",
                       "unreachable": "Could not reach the mail server."}.get(str(exc), "Could not connect.")
    return True, "Mail connected."


def save_mail_scope(client_id: int, folder: str, senders: str) -> bool:
    c = db.get_client_by_id(client_id)
    return bool(c) and mail.set_scope(c["phone"], folder.strip() or "INBOX", senders.strip())


def disconnect_mail(client_id: int) -> bool:
    c = db.get_client_by_id(client_id)
    return bool(c) and mail.disconnect(c["phone"])


def rewrap_mail() -> int:
    return mail.rewrap_all()


def run_backup() -> str:
    return backup.run_backup()


def handle_upgrade(request_id: int) -> bool:
    return upgrade.mark_handled(request_id)


def detail(client_id: int) -> dict | None:
    c = db.get_client_by_id(client_id)
    if not c:
        return None
    plan = plans.plan_of(c)  # in force now (a trial counts as Plus)
    usage = plans.usage_table(c["phone"])
    usage_rows = [{"key": k, "used": usage.get(k, 0), "limit": plans.limit(plan, k)} for k in plans.USAGE_KEYS]
    return {"c": c, "plan": plans.raw_plan(c), "cost": costs.estimate(usage),
            "trial": {"active": plans.trial_active(c) and plans.raw_plan(c) != "plus", "ends": trial.end_date(c),
                      "pending": c["trial_days"]}, "hour": abuse.last_hour(c), "abuse": {
                "huge_chars": config.ABUSE_HUGE_CHARS, "huge_per_hour": config.ABUSE_HUGE_PER_HOUR,
                "tokens_per_hour": config.ABUSE_TOKENS_PER_HOUR}, "usage": usage_rows, "month": plans.month_key(),
            "briefing_time": quiet.fmt(c["briefing_min"]) if c["briefing_min"] >= 0 else "",
            "memory": memory.load(c["phone"])["sections"], "watches": db.list_watch(c["phone"]),
            "mail": mail.public_info(c["phone"]) if vault.available() else None, "mail_history": mail.history(c["phone"], 8),
            "mail_available": vault.available(), "calling_on": settings.calling_on(),
            "pay": pay_info(c), "tz": config.TIMEZONE, "window": db.window_open(c["phone"]),
            "total_q": i18n.total_for(plan)}


def pay_info(c: dict) -> dict:
    due = billing.due_date(c)
    return {"on": config.BILLING_ON, "free": bool(c.get("free")), "due": due.astimezone(ZoneInfo(config.TIMEZONE)).strftime("%d.%m.%Y") if due else "",
            "paid_until": bool(c.get("paid_until")), "months": c.get("paid_months", 0), "code": c.get("pay_code") or "",
            "next_basic": billing.amount_due(c, "basic"), "next_plus": billing.amount_due(c, "plus"),
            "grace": bool(billing.in_grace(c, db.now_utc())), "unpaid_freeze": billing.is_unpaid_freeze(c),
            "payments": billing.payments_of(c["phone"], 8)}


def mark_paid(client_id: int, plan: str, months: int, amount: str = "") -> tuple[bool, str]:
    """The owner saw the money arrive: apply it. The amount is optional (the normal price is used if blank)."""
    c = db.get_client_by_id(client_id)
    if not c:
        return False, "Client not found."
    if plan not in plans.PLANS:
        return False, "Plan must be lite, basic or plus."
    if not 1 <= months <= 24:
        return False, "Months must be 1 to 24."
    total = None
    if amount.strip():
        total = receipt.parse_amount(amount)
        if total is None or total <= 0:
            return False, "The amount must be a number."
        total = int(round(total))
    updated = billing.apply_payment(c["phone"], plan, months, source="manual", amount=total)
    jobs.enqueue("send", c["phone"], {"text": billing.paid_text(updated), "proactive": True})
    return True, "Payment saved."


def set_free(client_id: int, on: bool) -> bool:
    c = db.get_client_by_id(client_id)
    if not c:
        return False
    billing.set_free(c["phone"], on)
    return True


def approve_payment(payment_id: int) -> tuple[bool, str]:
    return receipt.approve(payment_id)


def reject_payment(payment_id: int) -> bool:
    return receipt.reject(payment_id)


def add_client(phone: str, name: str, plan: str = "basic") -> tuple[bool, str]:
    normalised = db.norm_phone(phone)
    if not db.valid_phone(normalised):
        return False, "That does not look like a phone number. Use the country code, for example +90 555 123 45 67."
    if plan not in plans.PLANS:
        return False, "Plan must be lite, basic or plus."
    existing = db.get_client(normalised)
    db.upsert_client(normalised, " ".join((name or "").split())[:80],
                     existing["preferences"] if existing else "", 1)
    if not existing:  # an existing client keeps their plan; change it with set_plan
        db.set_client_fields(normalised, plan=plan)
        if plan == "basic" and config.TRIAL_DAYS > 0:  # the free Plus trial starts with their first message
            db.set_client_fields(normalised, trial_days=config.TRIAL_DAYS)
    return True, "Client saved."


def set_frozen(client_id: int, frozen: bool) -> bool:
    c = db.get_client_by_id(client_id)
    if not c:
        return False
    if frozen:
        abuse.freeze(c["phone"], "frozen by the owner in /admin")
    else:
        abuse.unfreeze(c["phone"])
    return True


def set_trial(client_id: int, days: int) -> tuple[bool, str]:
    """Give (or with 0, remove) a free Plus trial that runs from now."""
    c = db.get_client_by_id(client_id)
    if not c:
        return False, "Client not found."
    if not 0 <= days <= 90:
        return False, "Trial length must be 0 to 90 days."
    if days == 0:
        db.set_client_fields(c["phone"], trial_days=0, trial_ends_at="", trial_notes=0)
    else:
        db.set_client_fields(c["phone"], trial_days=0, trial_ends_at=db.to_iso(db.now_utc() + timedelta(days=days)),
                             trial_notes=0)
    return True, "Trial saved."


def set_plan(client_id: int, plan: str, briefing_time: str = "", work_area: str | None = None) -> tuple[bool, str]:
    c = db.get_client_by_id(client_id)
    if not c:
        return False, "Client not found."
    if plan not in plans.PLANS:
        return False, "Plan must be lite, basic or plus."
    fields: dict = {"plan": plan}
    if work_area is not None:
        fields["work_area"] = " ".join(work_area.split())[:80]
    when = (briefing_time or "").strip()
    if plan == "plus" and when:
        minutes = quiet.parse_hhmm(when)
        if minutes is None:
            return False, "Briefing time must look like 08:30."
        fields["briefing_min"] = minutes
    if plan in ("lite", "basic"):
        fields["briefing_min"] = -1  # the briefing is a Plus feature
        fields.update(trial_days=0, trial_ends_at="")  # choosing Basic by hand also ends any trial
    db.set_client_fields(c["phone"], **fields)
    return True, "Plan saved."


def toggle(client_id: int) -> bool:
    c = db.get_client_by_id(client_id)
    if not c:
        return False
    db.set_client_fields(c["phone"], active=0 if c["active"] else 1)
    return True


def ping(client_id: int) -> bool:
    c = db.get_client_by_id(client_id)
    if not c or not c["active"]:
        return False
    jobs.enqueue("send", c["phone"], {"hello": True})
    return True


def clear_memory(client_id: int) -> bool:
    c = db.get_client_by_id(client_id)
    if not c:
        return False
    memory.forget_all(c["phone"])
    return True


def delete_client(client_id: int, confirm: str) -> bool:
    """Erase a client completely. The last 4 digits of their number must be typed to confirm."""
    c = db.get_client_by_id(client_id)
    if not c or (confirm or "").strip() != db.last4(c["phone"]):
        return False
    db.erase_client(c["phone"])
    return True


def retry_job(job_id: int) -> bool:
    return jobs.retry_dead(job_id)


def referrals_page() -> dict:
    return {"rows": referrals.admin_rows(), "today": referrals.signups_today(), "cap": config.REF_SIGNUPS_PER_DAY,
            "friend_pct": config.REF_FRIEND_PCT, "ladder": config.REF_LADDER}
