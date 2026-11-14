"""Billing inside the chat: frozen-unpaid replies, receipts through the pipeline, trial and upgrade messages."""
import io
from datetime import timedelta

import pytest
from PIL import Image

from app import abuse, billing, config, db, jobs, plans, receipt, scheduler, trial, upgrade, whatsapp
from conftest import PHONE
from helpers import say

IBAN = "TR330006100519786457841326"


def png():
    out = io.BytesIO()
    Image.new("RGB", (60, 40), (255, 255, 255)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def bank(monkeypatch, basic_client):
    monkeypatch.setattr(config, "BILLING_ON", True)
    monkeypatch.setattr(config, "BANK_IBAN", IBAN)
    monkeypatch.setattr(config, "BANK_HOLDER", "Example Holder Ltd")
    monkeypatch.setattr(config, "BANK_NAME", "Test Bank")
    monkeypatch.setattr(whatsapp, "download_media", lambda ref: (png(), "image/png"))
    yield
    receipt.set_reader(None)


def freeze_unpaid():
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() - timedelta(days=5)))
    abuse.freeze(PHONE, billing.UNPAID)


def test_a_frozen_unpaid_client_who_writes_is_told_to_pay_first():
    freeze_unpaid()
    out = say("merhaba")
    assert len(out) == 1 and "ödeme" in out[0].lower() and "CG-" in out[0] and "Example Holder Ltd" in out[0]


def test_pay_first_is_not_sent_again_within_the_hour():
    freeze_unpaid()
    assert len(say("a")) == 1 and say("b") == []


def test_an_abuse_freeze_still_gets_the_old_notice_not_the_payment_text():
    abuse.freeze(PHONE, "huge prompts")
    out = say("merhaba")
    assert out and "CG-" not in out[0]


def test_a_receipt_from_a_frozen_unpaid_client_switches_them_on():
    freeze_unpaid()
    c = db.get_client(PHONE)
    code = billing.ensure_code(c)
    today = db.now_utc().astimezone(billing._tz()).strftime("%Y-%m-%d")
    receipt.set_reader(lambda blocks, phone: {"is_transfer_receipt": True, "amount": 1500, "currency": "TRY", "date": today,
                                              "recipient_iban": IBAN, "description": code, "reference_no": "FAST123456"})
    out = say("dekont", kind="image", media_id="m1", mime="image/png")
    c = db.get_client(PHONE)
    assert not c["frozen"] and c["paid_until"] and out and "alındı" in out[0]


def test_a_picture_that_is_not_a_receipt_from_a_frozen_client_gets_a_hint():
    freeze_unpaid()
    receipt.set_reader(lambda blocks, phone: {"is_transfer_receipt": False})
    out = say("", kind="image", media_id="m2", mime="image/png")
    assert out and "dekont" in out[0].lower()
    assert db.get_client(PHONE)["frozen"] == 1


def test_a_doubtful_receipt_reaches_the_owner_and_gives_grace():
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() + timedelta(days=1)))
    receipt.set_reader(lambda blocks, phone: {"is_transfer_receipt": True, "amount": 1500, "currency": "TRY", "date": "2020-01-01",
                                              "recipient_iban": IBAN, "description": "x"})
    out = say("ödeme", kind="image", media_id="m3", mime="image/png")
    assert db.get_client(PHONE)["grace_until"] and billing.review_queue() and out


def test_billing_off_leaves_photos_to_the_normal_flow(monkeypatch):
    monkeypatch.setattr(config, "BILLING_ON", False)
    assert not receipt.wanted(db.get_client(PHONE), "dekont", db.now_utc())


def test_trial_messages_carry_the_payment_details_when_billing_is_on():
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() + timedelta(hours=10)))
    assert trial.tick() == 1
    text = _last_text()
    assert "IBAN" in text and "CG-" in text and "Plus" in text


def test_trial_messages_are_the_old_ones_when_billing_is_off(monkeypatch):
    monkeypatch.setattr(config, "BILLING_ON", False)
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() + timedelta(hours=10)))
    trial.tick()
    assert "IBAN" not in _last_text()


def test_clients_who_already_paid_get_no_trial_notice():
    billing.apply_payment(PHONE, "basic")
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() + timedelta(hours=10)))
    assert trial.tick() == 0


def test_the_plus_word_gives_the_plus_payment_details():
    db.set_client_fields(PHONE, trial_ends_at="")
    out = say("plus")
    assert out and "IBAN" in out[0] and "Plus" in out[0]


def test_the_scheduler_runs_the_billing_check():
    assert "billing.tick" in open(scheduler.__file__).read()


def _last_text():
    import json
    with db.conn() as c:
        row = c.execute("SELECT payload FROM jobs WHERE kind='send' ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row["payload"])["text"]
