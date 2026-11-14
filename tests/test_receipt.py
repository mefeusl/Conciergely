"""Receipt (dekont) checks: apply only when everything fits, otherwise review with a grace period."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import abuse, billing, config, db, receipt
from conftest import PHONE

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
IBAN = "TR33 0006 1005 1978 6457 8413 26"


@pytest.fixture(autouse=True)
def bank(monkeypatch):
    monkeypatch.setattr(config, "BILLING_ON", True)
    monkeypatch.setattr(config, "BANK_IBAN", IBAN)
    monkeypatch.setattr(config, "BANK_HOLDER", "Example Holder Ltd")
    monkeypatch.setattr(config, "BANK_NAME", "Test Bank")
    yield
    receipt.set_reader(None)


@pytest.fixture
def owed(basic_client):
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(NOW + timedelta(days=2)))
    return db.get_client(PHONE)


def good(client, **over):
    data = {"is_transfer_receipt": True, "amount": 1500, "currency": "TRY", "date": "2026-11-03",
            "recipient_iban": IBAN.replace(" ", ""), "recipient_name": "Example Holder Ltd", "sender_name": "AYSE YILMAZ",
            "description": "Conciergely " + billing.ensure_code(client), "reference_no": "FAST123456"}
    data.update(over)
    return data


def run(client, data, image=b"img-1", caption=""):
    receipt.set_reader(lambda blocks, phone: data)
    return receipt.handle(db.get_client(PHONE), [{"type": "image"}], image, caption, now=NOW)


def payments(status=None):
    with db.conn() as c:
        rows = c.execute("SELECT * FROM payments" + (" WHERE status=?" if status else ""), (status,) if status else ()).fetchall()
    return [dict(r) for r in rows]


def test_a_perfect_receipt_switches_the_client_on(owed):
    reply = run(owed, good(owed))
    c = db.get_client(PHONE)
    assert "Ödemeniz alındı" in reply or "Payment received" in reply
    assert c["plan"] == "basic" and c["paid_months"] == 1 and c["setup_paid_tl"] == 1000
    assert db.from_iso(c["paid_until"]) > NOW + timedelta(days=28)
    assert len(payments("applied")) == 1 and not db.get_client(PHONE)["grace_until"]


def test_the_plus_amount_upgrades_to_plus(owed):
    reply = run(owed, good(owed, amount=3000))
    c = db.get_client(PHONE)
    assert c["plan"] == "plus" and c["setup_paid_tl"] == 2000 and "Plus" in reply


def test_a_frozen_unpaid_client_is_unfrozen_by_a_good_receipt(owed):
    abuse.freeze(PHONE, billing.UNPAID)
    run(owed, good(owed))
    assert not db.get_client(PHONE)["frozen"]


def test_wrong_account_is_refused_and_nothing_changes(owed):
    reply = run(owed, good(owed, recipient_iban="TR760006200000000000000001"))
    assert "IBAN" in reply and not db.get_client(PHONE)["paid_until"] and payments("applied") == []
    assert len(payments("rejected")) == 1


def test_masked_iban_matches_only_if_visible_digits_agree():
    assert receipt.iban_check("TR330006100519786457841326", IBAN) == "match"
    assert receipt.iban_check("TR33000610051978****841326", IBAN) == "match"
    assert receipt.iban_check("TR33000610051979****841326", IBAN) == "wrong"
    assert receipt.iban_check("TR33***", IBAN) == "unclear"
    assert receipt.iban_check("TR330006100519786457841327", IBAN) == "wrong"


def test_missing_code_goes_to_review_with_grace(owed):
    reply = run(owed, good(owed, description="kira"))
    c = db.get_client(PHONE)
    assert not c["paid_until"] and c["grace_until"] and "CG-" in reply
    assert len(payments("review")) == 1 and billing.review_queue()[0]["phone"] == PHONE


def test_wrong_amount_goes_to_review(owed):
    run(owed, good(owed, amount=1200))
    assert len(payments("review")) == 1 and not db.get_client(PHONE)["paid_until"]


def test_a_receipt_without_a_bank_reference_goes_to_review(owed):
    run(owed, good(owed, reference_no=""))
    assert len(payments("review")) == 1 and not db.get_client(PHONE)["paid_until"]


def test_old_date_goes_to_review(owed):
    run(owed, good(owed, date="2026-10-01"))
    assert len(payments("review")) == 1


def test_foreign_currency_goes_to_review(owed):
    run(owed, good(owed, currency="USD"))
    assert len(payments("review")) == 1


def test_the_same_receipt_cannot_be_used_twice(owed):
    run(owed, good(owed))
    again = run(owed, good(owed))
    assert "already used" in again or "kullanılmış" in again
    assert len(payments("applied")) == 1


def test_the_same_image_with_different_reading_is_still_caught(owed):
    run(owed, good(owed), image=b"same-picture")
    reply = run(owed, good(owed, reference_no="OTHER", sender_name="X"), image=b"same-picture")
    assert "kullanılmış" in reply or "already used" in reply


def test_an_unreadable_receipt_gives_grace_and_reaches_the_owner(owed):
    receipt.set_reader(lambda blocks, phone: None)
    reply = receipt.handle(db.get_client(PHONE), [{"type": "image"}], b"blur", "", now=NOW)
    assert db.get_client(PHONE)["grace_until"] and len(payments("review")) == 1 and reply


def test_a_photo_that_is_not_a_receipt_carries_on(owed):
    assert run(owed, {"is_transfer_receipt": False}) is None
    abuse.freeze(PHONE, billing.UNPAID)
    assert run(db.get_client(PHONE), {"is_transfer_receipt": False}) is not None  # a frozen client is told


def test_owner_approves_a_review_and_the_client_is_switched_on(owed):
    run(owed, good(owed, description="kira"))
    pid = billing.review_queue()[0]["id"]
    ok, _ = receipt.approve(pid, now=NOW)
    c = db.get_client(PHONE)
    assert ok and c["paid_until"] and payments("approved") and not receipt.approve(pid, now=NOW)[0]
    again = run(owed, good(owed, description="kira"), image=b"img-1")
    assert ("kullanılmış" in again or "already used" in again) and len(payments("applied")) == 1


def test_owner_can_reject_a_review(owed):
    run(owed, good(owed, description="kira"))
    pid = billing.review_queue()[0]["id"]
    assert receipt.reject(pid) and billing.review_queue() == [] and not db.get_client(PHONE)["paid_until"]


def test_amounts_are_parsed_in_turkish_and_english_formats():
    p = receipt.parse_amount
    assert p("1.500,00 TL") == 1500 and p("1,500.00") == 1500 and p("1500") == 1500 and p("1.500") == 1500
    assert p(2000) == 2000 and p("500,50") == 500.5 and p("") is None and p(None) is None and p(True) is None


def test_extract_tolerates_junk():
    ex = receipt.extract({"is_transfer_receipt": "yes", "amount": "abc", "recipient_iban": "tr33 0006"})
    assert ex.is_receipt is False and ex.amount is None and ex.iban == "TR330006"
    assert receipt.extract(None) is None


def test_wanted_only_when_a_payment_is_plausible(owed, basic_client):
    c = dict(owed)
    assert receipt.wanted(c, "", NOW)  # trial ends in 2 days
    db.set_client_fields(PHONE, trial_ends_at="", paid_until=db.to_iso(NOW + timedelta(days=25)))
    assert not receipt.wanted(db.get_client(PHONE), "", NOW) and receipt.wanted(db.get_client(PHONE), "dekont", NOW)
    db.set_client_fields(PHONE, free=1)
    assert not receipt.wanted(db.get_client(PHONE), "dekont", NOW)


def test_a_receipt_is_never_read_when_billing_is_off(owed, monkeypatch):
    monkeypatch.setattr(config, "BILLING_ON", False)
    assert not receipt.wanted(owed, "dekont", NOW)
