"""Billing by bank transfer: prices, half-price months, due dates, reminders, the freeze, free clients."""
from datetime import datetime, timedelta, timezone

import pytest

from app import abuse, billing, config, db, jobs, whatsapp
from conftest import PHONE

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def bank(monkeypatch):
    monkeypatch.setattr(config, "BILLING_ON", True)
    monkeypatch.setattr(config, "BANK_IBAN", "TR33 0006 1005 1978 6457 8413 26")
    monkeypatch.setattr(config, "BANK_HOLDER", "Example Holder Ltd")
    monkeypatch.setattr(config, "BANK_NAME", "Test Bank")


def _client(**fields):
    db.upsert_client(PHONE, name="Ayşe")
    db.set_client_fields(PHONE, onboarding_done=1, **fields)
    return db.get_client(PHONE)


def _sent():
    import json
    with db.conn() as c:
        rows = c.execute("SELECT kind, phone, payload FROM jobs WHERE kind='send' ORDER BY id").fetchall()
    return [{"phone": r["phone"], "payload": json.loads(r["payload"])} for r in rows]


# ---------- prices ----------

def test_first_two_months_are_half_price_then_full(basic_client):
    c = dict(basic_client)
    assert [billing.month_price("basic", n) for n in (0, 1, 2, 3)] == [500, 500, 1000, 1000]
    assert [billing.month_price("plus", n) for n in (0, 1, 2)] == [1000, 1000, 2000]


def test_first_payment_includes_the_setup_fee_once(basic_client):
    c = dict(basic_client)
    assert billing.amount_due(c, "basic") == 1500 and billing.amount_due(c, "plus") == 3000
    paid = billing.apply_payment(PHONE, "basic", now=T0)
    assert billing.amount_due(paid, "basic") == 500  # month 2, setup already paid
    assert billing.amount_due(paid, "plus") == 1000 + 1000  # month 2 at Plus half price + setup difference


def test_months_param_adds_each_month_at_its_price(basic_client):
    c = dict(basic_client)
    assert billing.amount_due(c, "basic", months=3) == 1000 + 500 + 500 + 1000


def test_add_months_clamps_to_month_end():
    assert billing.add_months(datetime(2026, 1, 31, tzinfo=timezone.utc), 1).day == 28


# ---------- applying a payment ----------

def test_payment_moves_paid_until_and_records_it(basic_client):
    c = billing.apply_payment(PHONE, "plus", now=T0)
    assert c["plan"] == "plus" and db.from_iso(c["paid_until"]) == datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
    assert c["paid_months"] == 1 and c["setup_paid_tl"] == 2000
    assert billing.payments_of(PHONE)[0]["amount_tl"] == 3000
    again = billing.apply_payment(PHONE, "plus", now=T0 + timedelta(days=10))
    assert db.from_iso(again["paid_until"]) == datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)  # extends, never shortens


def test_paying_before_the_trial_ends_keeps_the_remaining_days(basic_client):
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(T0 + timedelta(days=3)))
    c = billing.apply_payment(PHONE, "basic", now=T0)
    assert db.from_iso(c["paid_until"]) == datetime(2026, 12, 4, 12, 0, tzinfo=timezone.utc)


def test_paying_unfreezes_an_unpaid_client_but_not_an_abuse_freeze(basic_client):
    abuse.freeze(PHONE, billing.UNPAID)
    assert not billing.apply_payment(PHONE, "basic", now=T0)["frozen"]
    abuse.freeze(PHONE, "huge prompts")
    assert billing.apply_payment(PHONE, "basic", now=T0)["frozen"] == 1


def test_downgrading_to_basic_switches_the_briefing_off(client):
    db.set_client_fields(PHONE, briefing_min=480)
    assert billing.apply_payment(PHONE, "basic", now=T0)["briefing_min"] == -1


# ---------- reminders and freeze ----------

def test_reminders_go_out_at_minus_5_0_plus_2_and_freeze_at_plus_4(basic_client):
    due = T0
    db.set_client_fields(PHONE, plan="plus", paid_until=db.to_iso(due), paid_months=3, setup_paid_tl=2000)
    assert billing.tick(due - timedelta(days=6)) == {"reminded": 0, "frozen": 0}
    assert billing.tick(due - timedelta(days=5))["reminded"] == 1
    assert billing.tick(due - timedelta(days=4))["reminded"] == 0  # not twice
    assert billing.tick(due)["reminded"] == 1
    assert billing.tick(due + timedelta(days=2))["reminded"] == 1
    assert billing.tick(due + timedelta(days=3)) == {"reminded": 0, "frozen": 0}
    assert not db.get_client(PHONE)["frozen"]
    assert billing.tick(due + timedelta(days=4))["frozen"] == 1
    c = db.get_client(PHONE)
    assert c["frozen"] and billing.is_unpaid_freeze(c)
    texts = [j["payload"]["text"] for j in _sent()]
    assert len(texts) == 3 and all("TR33 0006" in t for t in texts)
    assert "CG-" in texts[0] and "2.000 TL" in texts[0]  # Plus, full price from month 4


def test_nothing_happens_when_billing_is_off(basic_client, monkeypatch):
    monkeypatch.setattr(config, "BILLING_ON", False)
    db.set_client_fields(PHONE, paid_until=db.to_iso(T0))
    assert billing.tick(T0 + timedelta(days=30)) == {"reminded": 0, "frozen": 0} and not db.get_client(PHONE)["frozen"]


def test_nothing_happens_without_bank_details(basic_client, monkeypatch):
    monkeypatch.setattr(config, "BANK_IBAN", "")
    db.set_client_fields(PHONE, paid_until=db.to_iso(T0))
    assert billing.tick(T0 + timedelta(days=30))["frozen"] == 0


def test_free_clients_are_never_billed(basic_client):
    db.set_client_fields(PHONE, paid_until=db.to_iso(T0), free=1)
    assert billing.tick(T0 + timedelta(days=30)) == {"reminded": 0, "frozen": 0}
    assert not db.get_client(PHONE)["frozen"]


def test_marking_free_unfreezes_an_unpaid_client(basic_client):
    abuse.freeze(PHONE, billing.UNPAID)
    billing.set_free(PHONE, True)
    assert not db.get_client(PHONE)["frozen"] and db.get_client(PHONE)["free"] == 1


def test_trial_clients_get_no_early_reminder_but_are_frozen_after_the_trial(basic_client):
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(T0))
    assert billing.tick(T0 - timedelta(days=5))["reminded"] == 0  # the trial's own 1-day notice covers this
    assert billing.tick(T0)["reminded"] == 1
    assert billing.tick(T0 + timedelta(days=4))["frozen"] == 1


def test_grace_period_holds_the_freeze_back(basic_client):
    db.set_client_fields(PHONE, paid_until=db.to_iso(T0), grace_until=db.to_iso(T0 + timedelta(days=6)))
    assert billing.tick(T0 + timedelta(days=5))["frozen"] == 0
    assert billing.tick(T0 + timedelta(days=7))["frozen"] == 1


def test_clients_without_any_due_date_are_never_touched(basic_client):
    assert billing.tick(T0 + timedelta(days=400)) == {"reminded": 0, "frozen": 0}


def test_an_abuse_frozen_client_is_left_alone(basic_client):
    db.set_client_fields(PHONE, paid_until=db.to_iso(T0))
    abuse.freeze(PHONE, "huge prompts")
    assert billing.tick(T0 + timedelta(days=30))["frozen"] == 0


def test_pay_code_is_stable_and_unique(basic_client):
    code = billing.ensure_code(dict(basic_client))
    assert code.startswith("CG-") and billing.ensure_code(db.get_client(PHONE)) == code
    db.upsert_client("+905559998877", name="B")
    assert billing.ensure_code(db.get_client("+905559998877")) != code


def test_payment_text_has_account_amounts_and_code(basic_client):
    text = billing.how_to_pay(dict(basic_client))
    assert "Example Holder Ltd" in text and "TR33 0006 1005 1978 6457 8413 26" in text
    assert "1.500 TL" in text and "3.000 TL" in text and "CG-" in text


# ---------- the admin side ----------

def test_admin_mark_paid_free_and_review_buttons(basic_client):
    from app import adminlogic, pages, receipt
    cid = db.get_client(PHONE)["id"]
    ok, _ = adminlogic.mark_paid(cid, "plus", 2)
    c = db.get_client(PHONE)
    assert ok and c["plan"] == "plus" and c["paid_months"] == 2 and billing.payments_of(PHONE)[0]["amount_tl"] == 2000 + 1000 + 1000
    assert adminlogic.mark_paid(cid, "gold", 1)[0] is False and adminlogic.mark_paid(cid, "basic", 0)[0] is False
    ok, _ = adminlogic.mark_paid(cid, "basic", 1, "1.250,50")
    assert ok and billing.payments_of(PHONE)[0]["amount_tl"] == 1250 and not adminlogic.mark_paid(cid, "basic", 1, "abc")[0]
    assert adminlogic.mark_paid(99999, "basic", 1)[0] is False
    assert adminlogic.set_free(cid, True) and db.get_client(PHONE)["free"] == 1 and adminlogic.set_free(cid, False)
    assert adminlogic.set_free(99999, True) is False
    # a review row shows up on the dashboard, with approve and reject buttons
    with db.conn() as c:
        c.execute("INSERT INTO payments(phone, at, amount_tl, plan, months, source, status, detail) VALUES(?,?,?,?,?,?,?,?)",
                  (PHONE, db.now_utc_iso(), 1500, "basic", 1, "receipt", "review", "x"))
    common = {"bot": "Conciergely", "csrf": "t"}
    home = pages.render("dashboard", **common, **adminlogic.dashboard())
    assert "Receipts to check" in home and "/admin/payments/" in home and "Money arrived" in home
    detail = pages.render("detail", **common, **adminlogic.detail(cid))
    assert "Mark as paid" in detail and "Family and friends" in detail and "CG-" not in detail.replace("made on first use", "")
    pid = billing.review_queue()[0]["id"]
    assert adminlogic.approve_payment(pid)[0] and adminlogic.reject_payment(pid) is False
    settings_html = pages.render("settings", **common, **adminlogic.settings_page())
    assert "Billing is <b>ON</b>" in settings_html and "Example Holder Ltd" in settings_html
