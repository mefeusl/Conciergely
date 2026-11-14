"""Paying by bank transfer: prices, due dates, reminders, the freeze, and applying a payment.

How it works:
  * Each client has a `paid_until` date. A payment moves it one month on (from the later of now and the old due date).
  * Before the first payment the due date is the end of the free trial.
  * The first `HALF_PRICE_MONTHS` paid months cost half; the setup fee is charged once (`setup_paid_tl` remembers it,
    so a Basic client who upgrades only owes the difference).
  * Reminders go out 5 days before, on the day, and 2 days after the due date. 4 days after, an unpaid client is frozen
    (frozen_reason 'unpaid'); their next message gets a "pay first" reply, and a receipt can still be sent.
  * Family and friends are marked `free` and are never billed.
  * Nothing here runs unless BILLING_ON=1.

All money is whole Turkish lira. The functions below take `now` so tests can control time.
"""
from __future__ import annotations

import calendar
import logging
import random
from datetime import datetime, timedelta

from . import abuse, config, db, i18n, jobs, ops, plans, referrals

log = logging.getLogger(__name__)

UNPAID = "unpaid"  # clients.frozen_reason for a freeze caused by a missed payment
BEFORE, DUE, LATE = 1, 2, 4  # bits in clients.pay_notes: which reminders were already sent for this due date


# ---------- prices ----------

def monthly(plan: str) -> int:
    return {"plus": config.PRICE_PLUS_MONTH, "lite": config.PRICE_LITE_MONTH}.get(plan, config.PRICE_BASIC_MONTH)


def setup_fee(plan: str) -> int:
    return {"plus": config.SETUP_PLUS, "lite": config.SETUP_LITE}.get(plan, config.SETUP_BASIC)


def month_price(plan: str, paid_months: int) -> int:
    """What the next month costs: reduced for the first HALF_PRICE_MONTHS paid months."""
    full = monthly(plan)
    if paid_months < config.HALF_PRICE_MONTHS:
        return round(full * config.HALF_PRICE_PERCENT / 100)
    return full


def setup_owed(client: dict, plan: str) -> int:
    return max(0, setup_fee(plan) - int(client.get("setup_paid_tl") or 0))


def amount_due(client: dict, plan: str | None = None, months: int = 1) -> int:
    """Total for `months` months on `plan`, with any setup fee still owed added once."""
    plan = plan or plans.raw_plan(client)
    paid = int(client.get("paid_months") or 0)
    total = setup_owed(client, plan)
    off = referrals.discount_pct(client)  # referral discounts touch the first month only, never the setup fee
    for i in range(months):
        price = month_price(plan, paid + i)
        total += round(price * (100 - off) / 100) if i == 0 else price
    return total


def add_months(moment: datetime, months: int) -> datetime:
    month_index = moment.month - 1 + months
    year, month = moment.year + month_index // 12, month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


# ---------- state ----------

def due_date(client: dict) -> datetime | None:
    """When the client must pay next: the paid-until date, or the end of the free trial before the first payment."""
    return db.from_iso(client.get("paid_until") or "") or db.from_iso(client.get("trial_ends_at") or "")


def billable(client: dict) -> bool:
    return bool(client["active"] and not client.get("free") and due_date(client))


def is_unpaid_freeze(client: dict) -> bool:
    return bool(client.get("frozen") and client.get("frozen_reason") == UNPAID)


def in_grace(client: dict, now: datetime) -> bool:
    until = db.from_iso(client.get("grace_until") or "")
    return bool(until and until > now)


def ensure_code(client: dict) -> str:
    """The short code the client writes in the transfer description. Made once, never reused by another client."""
    if client.get("pay_code"):
        return client["pay_code"]
    with db.conn() as c:
        taken = {r["pay_code"] for r in c.execute("SELECT pay_code FROM clients WHERE pay_code != ''")}
    code = ""
    while not code or code in taken:
        code = "CG-%05d" % random.randint(0, 99999)
    db.set_client_fields(client["phone"], pay_code=code)
    client["pay_code"] = code
    return code


def lira(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + " TL"


def iban_pretty(iban: str) -> str:
    raw = "".join(iban.split()).upper()
    return " ".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def how_to_pay(client: dict, plan: str | None = None) -> str:
    """The payment instructions text for this client (language of the client)."""
    plan = plan or plans.raw_plan(client)
    amounts = " | ".join(f"{p.capitalize()}: {lira(amount_due(client, p))}" for p in plans.upward(plan))
    text = i18n.t(client["lang"], "pay_howto", amounts=amounts,
                  holder=config.BANK_HOLDER, bank=config.BANK_NAME, iban=iban_pretty(config.BANK_IBAN),
                  code=ensure_code(client))
    return text + (" " + i18n.t(client["lang"], "pay_upgrade_hint") if plan != "plus" else "")


def configured() -> bool:
    return bool(config.BANK_IBAN.strip() and config.BANK_HOLDER.strip())


# ---------- applying a payment ----------

def apply_payment(phone: str, plan: str, months: int = 1, source: str = "manual", amount: int | None = None,
                  fingerprint: str = "", detail: str = "", now: datetime | None = None) -> dict:
    """Record a payment and move the client's paid-until date. Returns the updated client."""
    now = now or db.now_utc()
    client = db.get_client(phone)
    if not client:
        raise ValueError("unknown client")
    if plan not in plans.PLANS or not 1 <= months <= 24:
        raise ValueError("bad plan or months")
    due = amount_due(client, plan, months)  # already includes any referral discount
    _, credit_ids = referrals.discount(client)
    total = amount if amount is not None else due
    base = max(now, due_date(client) or now)
    until = base
    for _ in range(months):
        until = add_months(until, 1)
    with db.conn() as c:
        c.execute("INSERT INTO payments(phone, at, amount_tl, plan, months, source, status, fingerprint, detail) "
                  "VALUES(?,?,?,?,?,?,?,?,?)",
                  (client["phone"], db.to_iso(now), int(total), plan, months, source, "applied", fingerprint, detail[:500]))
    fields = dict(plan=plan, paid_until=db.to_iso(until), paid_months=int(client.get("paid_months") or 0) + months,
                  setup_paid_tl=max(int(client.get("setup_paid_tl") or 0), setup_fee(plan)), pay_notes=0, grace_until="")
    if plan in ("lite", "basic"):
        fields["briefing_min"] = -1  # the briefing is a Plus feature
    db.set_client_fields(client["phone"], **fields)
    if total <= due:  # the discounted amount was paid, so the credits behind it are used up
        referrals.consume(credit_ids)
    referrals.on_payment(client, int(total))
    if is_unpaid_freeze(client):
        abuse.unfreeze(client["phone"])
    return db.get_client(client["phone"])  # type: ignore[return-value]


def paid_text(client: dict) -> str:
    until = db.from_iso(client["paid_until"])
    return i18n.t(client["lang"], "pay_received", plan=client["plan"].capitalize(),
                  date=until.astimezone(_tz()).strftime("%d.%m.%Y") if until else "") + referrals.mention(client, referrals.PAID_BIT)


def _tz():
    from zoneinfo import ZoneInfo
    return ZoneInfo(config.TIMEZONE)


def payments_of(phone: str, limit: int = 10) -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT * FROM payments WHERE phone=? ORDER BY id DESC LIMIT ?", (db.norm_phone(phone), limit)).fetchall()
    return [dict(r) for r in rows]


def review_queue() -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT p.*, COALESCE(cl.name,'') AS name FROM payments p LEFT JOIN clients cl ON cl.phone=p.phone "
                         "WHERE p.status='review' ORDER BY p.id").fetchall()
    return [dict(r) for r in rows]


def set_free(phone: str, free: bool) -> None:
    fields: dict = {"free": 1 if free else 0}
    client = db.get_client(phone)
    db.set_client_fields(phone, **fields)
    if free and client and is_unpaid_freeze(client):
        abuse.unfreeze(phone)


# ---------- the daily check ----------

def _queue(client: dict, kind: str, due: datetime) -> bool:
    text = i18n.t(client["lang"], kind, date=due.astimezone(_tz()).strftime("%d.%m.%Y"),
                  howto=how_to_pay(client))
    return jobs.enqueue("send", client["phone"], {"text": text, "proactive": True},
                        dedup_key=f"{kind}:{client['phone']}:{db.to_iso(due)}") is not None


def tick(now: datetime | None = None) -> dict:
    """Send due reminders and freeze clients who are 4 days late. Returns counts. Does nothing unless billing is on."""
    now = now or db.now_utc()
    out = {"reminded": 0, "frozen": 0}
    if not config.BILLING_ON or not configured():
        return out
    for client in db.list_clients():
        if not billable(client) or client["frozen"] and not is_unpaid_freeze(client):
            continue
        due = due_date(client)
        notes = int(client["pay_notes"])
        has_paid = bool(client.get("paid_until"))  # before the first payment the trial messages already warn them
        sent = None
        if now >= due + timedelta(days=config.FREEZE_DAYS_AFTER):
            if not client["frozen"] and not in_grace(client, now):
                abuse.freeze(client["phone"], UNPAID)
                ops.event("billing", f"{client.get('name') or client['phone']} was frozen: payment is "
                          f"{config.FREEZE_DAYS_AFTER} days late", dedup_key=f"unpaid-freeze:{client['phone']}:{db.to_iso(due)}")
                out["frozen"] += 1
            continue
        if now >= due + timedelta(days=config.LAST_REMINDER_DAYS_AFTER) and not notes & LATE:
            sent, notes = "pay_late", notes | LATE | DUE | BEFORE
        elif now >= due and not notes & DUE:
            sent, notes = "pay_due", notes | DUE | BEFORE
        elif has_paid and now >= due - timedelta(days=config.REMIND_DAYS_BEFORE) and not notes & BEFORE and now < due:
            sent, notes = "pay_before", notes | BEFORE
        if sent:
            db.set_client_fields(client["phone"], pay_notes=notes)
            if _queue(client, sent, due):
                out["reminded"] += 1
    return out


def pay_first_text(client: dict) -> str:
    return i18n.t(client["lang"], "pay_first", howto=how_to_pay(client))
