import json
from datetime import timedelta

from app import adminlogic, assistant, config, db, plans, tools, trial, whatsapp
import pytest

from conftest import FakeAnthropic, response, text_block
from helpers import drain, open_window, say

PHONE = "+905551234567"


@pytest.fixture(autouse=True)
def fake_model():
    assistant.set_client(FakeAnthropic(*[response(text_block("Tamam"))] * 40))
    yield
    assistant.set_client(None)


def _trial_client(days=7):
    db.set_client_fields(PHONE, plan="basic", trial_days=days, trial_ends_at="", trial_notes=0)


def test_new_basic_client_gets_pending_trial_and_it_starts_on_first_message():
    ok, _ = adminlogic.add_client("+905559990001", "Ayşe")
    c = db.get_client("+905559990001")
    assert c["trial_days"] == 7 and c["trial_ends_at"] == "" and plans.plan_of(c) == "basic"
    say("merhaba", phone="+905559990001")
    c = db.get_client("+905559990001")
    assert c["trial_days"] == 0 and c["trial_ends_at"] and plans.plan_of(c) == "plus"
    ends = db.from_iso(c["trial_ends_at"])
    assert timedelta(days=6, hours=23) < ends - db.now_utc() <= timedelta(days=7)


def test_trial_starts_only_once(basic_client):
    _trial_client(7)
    say("merhaba")
    first = db.get_client(PHONE)["trial_ends_at"]
    db.set_client_fields(PHONE, trial_days=3)  # even if days are set again, a running trial is not restarted
    say("selam")
    assert db.get_client(PHONE)["trial_ends_at"] == first


def test_plus_client_and_new_plus_client_get_no_trial():
    adminlogic.add_client("+905559990002", "Plus kişi", "plus")
    assert db.get_client("+905559990002")["trial_days"] == 0


def test_trial_unlocks_plus_tools_then_expiry_locks_them(basic_client):
    assert plans.gate(db.get_client(PHONE), "scenario") is not None
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() + timedelta(days=2)))
    assert plans.gate(db.get_client(PHONE), "scenario") is None
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() - timedelta(seconds=1)))
    assert plans.gate(db.get_client(PHONE), "scenario") is not None


def test_trial_prompt_line(basic_client):
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() + timedelta(days=2)))
    assert "free 7-day trial" in plans.describe(db.get_client(PHONE))


def test_one_day_notice_then_ended_notice_once_each(basic_client):
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() + timedelta(days=3)))
    assert trial.tick() == 0  # too early
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() + timedelta(hours=20)))
    open_window()
    whatsapp.OUTBOX.clear()
    assert trial.tick() == 1 and trial.tick() == 0
    drain()
    assert len(whatsapp.OUTBOX) == 1 and "1 gün" in whatsapp.OUTBOX[0][1]
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() - timedelta(minutes=5)))
    whatsapp.OUTBOX.clear()
    assert trial.tick() == 1 and trial.tick() == 0
    drain()
    assert len(whatsapp.OUTBOX) == 1 and "sona erdi" in whatsapp.OUTBOX[0][1]


def test_expired_before_headsup_sends_only_the_ended_notice(basic_client):
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() - timedelta(hours=1)))
    assert trial.tick() == 1 and trial.tick() == 0


def test_no_notices_for_plus_frozen_or_inactive(client):
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() - timedelta(hours=1)))
    assert trial.tick() == 0  # paying Plus client
    db.set_client_fields(PHONE, plan="basic", frozen=1)
    assert trial.tick() == 0
    db.set_client_fields(PHONE, frozen=0, active=0)
    assert trial.tick() == 0


def test_greeting_mentions_trial():
    adminlogic.add_client("+905559990004", "Yeni")
    out = say("merhaba", phone="+905559990004")
    assert any("ücretsiz Plus deneme" in t for t in out)


def test_admin_set_trial_and_remove(basic_client):
    cid = db.get_client(PHONE)["id"]
    assert adminlogic.set_trial(cid, 10)[0]
    c = db.get_client(PHONE)
    assert plans.trial_active(c) and plans.plan_of(c) == "plus"
    assert adminlogic.set_trial(cid, 0)[0] and plans.plan_of(db.get_client(PHONE)) == "basic"
    assert not adminlogic.set_trial(cid, 91)[0] and not adminlogic.set_trial(cid, -1)[0]
    assert not adminlogic.set_trial(9999, 5)[0]


def test_set_plan_basic_ends_trial(basic_client):
    cid = db.get_client(PHONE)["id"]
    adminlogic.set_trial(cid, 5)
    adminlogic.set_plan(cid, "basic")
    assert plans.plan_of(db.get_client(PHONE)) == "basic"


def test_trial_zero_days_config_gives_no_trial(monkeypatch):
    monkeypatch.setattr(config, "TRIAL_DAYS", 0)
    adminlogic.add_client("+905559990003", "X")
    assert db.get_client("+905559990003")["trial_days"] == 0


def test_flights_work_on_basic_without_trial(basic_client, monkeypatch):
    from app import flights
    monkeypatch.setattr(flights, "search", lambda *a, **k: {"offers": []})
    out = json.loads(tools.run(PHONE, "flight_search", {"origin": "IST", "destination": "LHR", "date": "2026-12-01"}))
    assert out.get("error") != "plus_feature"


def test_no_headsup_more_than_a_day_out(basic_client):
    db.set_client_fields(PHONE, trial_ends_at=db.to_iso(db.now_utc() + timedelta(hours=30)))
    assert trial.tick() == 0
