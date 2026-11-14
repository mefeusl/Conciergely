"""Reminder texts are encrypted at rest, readable only through the db functions, and never shown in /admin."""
import pytest

from app import adminlogic, config, db, vault
from conftest import PHONE

DUE = "2030-01-01T09:00:00+00:00"


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", vault.new_key())
    monkeypatch.setattr(config, "MAIL_MASTER_KEY_OLD", "")


def raw_texts():
    with db.conn() as c:
        return [r["text"] for r in c.execute("SELECT text FROM reminders ORDER BY id")]


def test_text_is_sealed_in_the_database_and_read_back_normally(client, key):
    db.add_reminder(PHONE, "call the lawyer about the divorce", DUE)
    assert "divorce" not in raw_texts()[0] and raw_texts()[0].startswith("enc:")
    assert db.list_pending_reminders(PHONE)[0]["text"] == "call the lawyer about the divorce"
    assert db.due_reminders("2031-01-01T00:00:00+00:00")[0]["text"] == "call the lawyer about the divorce"


def test_without_a_key_it_still_works_unencrypted(client, monkeypatch):
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", "")
    db.add_reminder(PHONE, "plain", DUE)
    assert raw_texts() == ["plain"] and db.list_pending_reminders(PHONE)[0]["text"] == "plain"


def test_old_plain_reminders_are_sealed_on_startup(client, monkeypatch):
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", "")
    db.add_reminder(PHONE, "old secret", DUE)
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", vault.new_key())
    db.init_db()
    assert raw_texts()[0].startswith("enc:") and "old secret" not in raw_texts()[0]
    assert db.list_pending_reminders(PHONE)[0]["text"] == "old secret"
    db.init_db()  # idempotent: nothing is sealed twice
    assert db.list_pending_reminders(PHONE)[0]["text"] == "old secret"


def test_a_text_sealed_for_one_client_does_not_open_for_another(client, key):
    db.add_reminder(PHONE, "mine", DUE)
    with db.conn() as c:
        c.execute("UPDATE reminders SET phone='+905559999999'")
    row = db._open_reminder({"id": 1, "phone": "+905559999999", "text": raw_texts()[0]})
    assert row["text"] == "(reminder text unavailable)"


def test_key_rotation_keeps_old_reminders_readable(client, monkeypatch):
    old = vault.new_key()
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", old)
    monkeypatch.setattr(config, "MAIL_MASTER_KEY_OLD", "")
    db.add_reminder(PHONE, "before rotation", DUE)
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", vault.new_key())
    monkeypatch.setattr(config, "MAIL_MASTER_KEY_OLD", old)
    assert db.list_pending_reminders(PHONE)[0]["text"] == "before rotation"


def test_a_lost_key_does_not_crash_the_scheduler(client, monkeypatch):
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", vault.new_key())
    db.add_reminder(PHONE, "secret", DUE)
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", vault.new_key())  # a different key, old one gone
    assert db.due_reminders("2031-01-01T00:00:00+00:00")[0]["text"] == "(reminder text unavailable)"


def test_admin_detail_has_no_reminders(client, key):
    db.add_reminder(PHONE, "very private", DUE)
    detail = adminlogic.detail(client["id"])
    assert "reminders" not in detail and "very private" not in str(detail)
