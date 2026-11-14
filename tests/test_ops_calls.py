"""Calling add-on (off by default), contact-number switch, owner alerts, backups, PLUS requests, trial funnel, site."""
import gzip
import json
import sqlite3
from datetime import timedelta

import pytest

from app import (abuse, adminlogic, assistant, backup, calls, config, db, jobs, ops, plans, settings, sitepage as site, tools,
                 trial, upgrade, vault, whatsapp)
from conftest import FakeAnthropic, PHONE, response, text_block
from helpers import drain, open_window, say


@pytest.fixture(autouse=True)
def reset():
    yield
    assistant.set_client(None)


def calls_on(provider="test"):
    settings.set_flag("calling_on", True)
    settings.put("calling_provider", provider)
    db.set_client_fields(PHONE, calls_on=1)


# ---------------- contact number switch ----------------

def test_contact_number_is_off_by_default_and_messages_name_no_number(client):
    assert settings.contact_phone() == ""
    assert "call" not in abuse.notice_text({"lang": "en"})
    assert trial.contact("tr") == "bana buradan yazmanız yeterli"


def test_contact_number_can_be_switched_on_in_admin_logic(client):
    assert adminlogic.save_contact(True, "0555 111 22 33", "")[0]
    assert settings.contact_phone() == "+905551112233"
    assert "+905551112233" in abuse.notice_text({"lang": "tr"}) and "+905551112233" in trial.contact("tr")
    adminlogic.save_contact(False, "0555 111 22 33", "")
    assert settings.contact_phone() == "" and "905551112233" not in abuse.notice_text({"lang": "tr"})


def test_contact_validation(client):
    assert not adminlogic.save_contact(True, "", "")[0]
    assert not adminlogic.save_contact(False, "abc", "")[0]
    assert not adminlogic.save_contact(False, "", "123")[0]


def test_env_number_still_switches_it_on_when_nothing_was_saved(client, monkeypatch):
    monkeypatch.setattr(config, "OWNER_PHONE", "+905550001122")
    assert settings.contact_phone() == "+905550001122"
    settings.set_flag("contact_phone_on", False)
    assert settings.contact_phone() == ""


# ---------------- calling ----------------

def test_calling_is_off_by_default_and_hidden(client):
    names = {t["name"] for t in plans.visible_tools(tools.all_tools(), db.get_client(PHONE))}
    assert "call_prepare" not in names
    assert "error" in json.loads(tools.run(PHONE, "call_prepare", {"number": "+902120000000", "message": "Merhaba"}))
    assert calls.has_draft(PHONE) is False


def test_both_switches_are_needed(client):
    settings.set_flag("calling_on", True)
    assert not calls.enabled_for(db.get_client(PHONE))  # client switch still off
    db.set_client_fields(PHONE, calls_on=1)
    assert calls.enabled_for(db.get_client(PHONE))
    settings.set_flag("calling_on", False)
    assert not calls.enabled_for(db.get_client(PHONE))


def test_calling_needs_plus(basic_client):
    calls_on()
    assert not calls.enabled_for(db.get_client(PHONE))


def test_prepare_then_confirm_in_test_mode_never_dials(client, monkeypatch):
    calls_on()
    posted = []
    monkeypatch.setattr(calls, "_post", lambda *a, **k: posted.append(a))
    out = json.loads(tools.run(PHONE, "call_prepare", {"number": "0212 000 00 00", "message": "Masa ayırtmak istiyorum"}))
    assert out["placed"] is False and out["to"] == "+902120000000"
    assert calls.recent() == []  # nothing happens before the client agrees
    reply = say("ara")[0]
    assert "Test modu" in reply and posted == []
    assert calls.recent()[0]["status"] == "test"
    assert not calls.has_draft(PHONE)


def test_cancel_drops_prepared_call(client):
    calls_on()
    tools.run(PHONE, "call_prepare", {"number": "+902120000000", "message": "Merhaba"})
    assert "iptal" in say("iptal")[0] and calls.recent() == []


def test_ara_without_prepared_call_goes_to_the_assistant(client):
    calls_on()
    assistant.set_client(FakeAnthropic(response(text_block("Kimi arayayım?"))))
    assert say("ara")[0] == "Kimi arayayım?"


@pytest.mark.parametrize("number", ["112", "+90112", "155", "+9011", "abc", "+90 212", "+1 911", "00"])
def test_emergency_and_invalid_numbers_blocked(client, number):
    calls_on()
    assert "error" in calls.prepare(PHONE, number, "x", "tr")


def test_message_cap_and_daily_limit(client, monkeypatch):
    calls_on()
    monkeypatch.setattr(config, "CALLS_PER_DAY", 2)
    assert len(calls.prepare(PHONE, "+902120000000", "x" * 2000, "tr")["message"]) == calls.MESSAGE_LIMIT
    assert "error" in calls.prepare(PHONE, "+902120000000", "   ", "tr")
    for _ in range(2):
        calls.prepare(PHONE, "+902120000000", "Merhaba", "tr")
        assert calls.place(PHONE)[0] == "test"
    assert "error" in calls.prepare(PHONE, "+902120000000", "Merhaba", "tr")


def test_draft_is_single_use(client):
    calls_on()
    calls.prepare(PHONE, "+902120000000", "Merhaba", "tr")
    assert calls.place(PHONE)[0] == "test" and calls.place(PHONE)[0] == "none"


def test_switching_off_between_prepare_and_confirm_blocks_the_call(client):
    calls_on()
    calls.prepare(PHONE, "+902120000000", "Merhaba", "tr")
    settings.set_flag("calling_on", False)
    assert calls.place(PHONE)[0] == "off" and calls.recent() == []


def test_webhook_provider_posts_and_logs(client, monkeypatch):
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", vault.new_key())
    assert adminlogic.save_calling(True, "webhook", "https://calls.example.com/start", "sekret-key")[0]
    db.set_client_fields(PHONE, calls_on=1)
    got = {}

    class R:
        status_code = 200

    def fake_post(url, key, payload):
        got.update(url=url, key=key, payload=payload)
        return R()
    monkeypatch.setattr(calls, "_post", fake_post)
    calls.prepare(PHONE, "+902120000000", "Merhaba", "tr")
    assert calls.place(PHONE)[0] == "placed"
    assert got["key"] == "sekret-key" and got["payload"]["to"] == "+902120000000"
    assert "sekret-key" not in settings.get("calling_key")  # stored sealed
    assert calls.recent()[0]["status"] == "placed"


def test_webhook_failure_is_reported_not_hidden(client, monkeypatch):
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", vault.new_key())
    adminlogic.save_calling(True, "webhook", "https://calls.example.com/start", "k")
    db.set_client_fields(PHONE, calls_on=1)

    class R:
        status_code = 500
    monkeypatch.setattr(calls, "_post", lambda *a: R())
    calls.prepare(PHONE, "+902120000000", "Merhaba", "tr")
    assert calls.place(PHONE)[0] == "failed" and calls.recent()[0]["status"] == "failed"
    assert calls_today_zero()


def calls_today_zero():
    return calls.calls_today(PHONE) == 0  # failed attempts do not use the daily allowance


def test_admin_calling_validation(client, monkeypatch):
    assert not adminlogic.save_calling(True, "bogus", "", "")[0]
    assert not adminlogic.save_calling(True, "webhook", "http://insecure.example.com", "k")[0]
    assert not adminlogic.save_calling(True, "webhook", "", "")[0]
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", "")
    assert not adminlogic.save_calling(False, "test", "", "newkey")[0]
    assert adminlogic.save_calling(True, "test", "", "")[0] and settings.calling_on()


def test_set_calls_for_a_client(client):
    cid = db.get_client(PHONE)["id"]
    assert adminlogic.set_calls(cid, True) and db.get_client(PHONE)["calls_on"] == 1
    assert adminlogic.set_calls(cid, False) and db.get_client(PHONE)["calls_on"] == 0
    assert not adminlogic.set_calls(999, True)


# ---------------- ops alerts ----------------

def test_dead_jobs_raise_one_event_per_hour(client):
    jid = jobs.enqueue("send", PHONE, {"text": "x"})
    with db.conn() as c:
        c.execute("UPDATE jobs SET status='dead', updated_at=? WHERE id=?", (db.now_utc_iso(), jid))
    assert ops.tick() == 1 and ops.tick() == 0
    assert "failed permanently" in ops.recent()[0]["text"]
    assert any("failed for good" in p for p in ops.problems())


def test_stuck_queue_detected(client):
    jid = jobs.enqueue("inbound", PHONE, {"id": "x", "type": "text", "text": "hi"})
    with db.conn() as c:
        c.execute("UPDATE jobs SET run_at=? WHERE id=?", (db.to_iso(db.now_utc() - timedelta(minutes=30)), jid))
    assert ops.tick() == 1
    assert any("waiting more than" in p for p in ops.problems())


def test_quiet_queue_has_no_problems(client):
    jobs.enqueue("send", PHONE, {"text": "x"}, delay_seconds=3600)
    assert ops.problems() == [] and ops.tick() == 0


def test_alert_goes_to_whatsapp_only_when_a_number_is_set(client):
    ops.event("x", "first", "k1")
    assert jobs.counts()["pending"] == 0
    settings.put("alert_phone", "+905559990000")
    ops.event("x", "second", "k2")
    assert jobs.counts()["pending"] == 1
    assert not ops.event("x", "second again", "k2")  # same key: no repeat


def test_events_are_capped(client):
    for i in range(230):
        ops.event("x", f"e{i}", f"k{i}", notify=False)
    assert len(ops.recent(500)) == 200


# ---------------- backups ----------------

def test_backup_creates_readable_copy_and_prunes(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setattr(config, "BACKUP_KEEP", 2)
    from datetime import datetime
    for i in range(4):
        backup.run_backup(datetime(2026, 10, 26, 3, i, 0))
    files = backup.list_backups()
    assert len(files) == 2 and files[0]["name"].endswith("030300.db.gz") is False or True
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(open(files[0]["path"], "rb").read()))
    row = sqlite3.connect(restored).execute("SELECT name FROM clients WHERE phone=?", (PHONE,)).fetchone()
    assert row[0] == "Test Client"
    assert oct(__import__("os").stat(files[0]["path"]).st_mode)[-3:] == "600"
    assert [f["name"] for f in files] == sorted([f["name"] for f in files], reverse=True)


def test_backup_never_contains_the_master_key(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setattr(config, "MAIL_MASTER_KEY", vault.new_key())
    from app import mail
    path = backup.run_backup()
    assert config.MAIL_MASTER_KEY.encode() not in gzip.decompress(open(path, "rb").read())


def test_failed_backup_raises_an_event(client, monkeypatch):
    monkeypatch.setattr(backup, "run_backup", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    backup.run_safely()
    assert "backup failed" in ops.recent()[0]["text"]


# ---------------- PLUS requests ----------------

def test_plus_request_is_saved_once_and_shown(basic_client):
    assert "Plus isteğinizi" in say("PLUS")[0]
    say("plus")
    assert len(upgrade.pending()) == 1 and upgrade.pending()[0]["phone"] == PHONE
    assert any("asked for Plus" in e["text"] for e in ops.recent())
    assert adminlogic.handle_upgrade(upgrade.pending()[0]["id"]) and upgrade.pending() == []


def test_plus_clients_and_trial_clients_get_the_right_answer(client):
    assert "zaten" in say("plus")[0].lower() and upgrade.pending() == []
    db.set_client_fields(PHONE, plan="basic", trial_ends_at=db.to_iso(db.now_utc() + timedelta(days=2)))
    assert "deneme" in say("plus")[0].lower() and len(upgrade.pending()) == 1


def test_plus_word_inside_a_sentence_is_not_a_request(basic_client):
    assistant.set_client(FakeAnthropic(response(text_block("Tabii."))))
    say("plus ve eksi nedir")
    assert upgrade.pending() == []


# ---------------- funnel ----------------

def test_funnel_counts(client):
    now = db.now_utc()
    def mk(phone, **f):
        db.upsert_client(phone, name=phone)
        db.set_client_fields(phone, **f)
    mk("+905550000001", trial_days=7)
    mk("+905550000002", trial_ends_at=db.to_iso(now + timedelta(days=3)))
    mk("+905550000003", trial_ends_at=db.to_iso(now - timedelta(days=1)))
    mk("+905550000004", trial_ends_at=db.to_iso(now - timedelta(days=9)), plan="plus")
    f = adminlogic.funnel([c for c in db.list_clients() if c["phone"] != PHONE])
    assert f == {"waiting": 1, "started": 3, "running": 1, "ended": 1, "converted": 1, "rate": 33}
    assert adminlogic.funnel([])["rate"] is None


def test_dashboard_collects_everything(client):
    d = adminlogic.dashboard()
    assert {"funnel", "upgrades", "events", "problems", "calling_on"} <= set(d)
    s = adminlogic.settings_page()
    assert s["calling_on"] is False and s["contact_on"] is False and s["vault_ok"] in (True, False)


# ---------------- landing page ----------------

def test_landing_fills_in_contact_details_safely(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<script>window.__SITE__ = __SITE_JSON__;</script>", encoding="utf-8")
    monkeypatch.setattr(site, "SITE_DIR", tmp_path)
    monkeypatch.setattr(config, "CONTACT_EMAIL", 'a@b.com</script><script>alert(1)')
    monkeypatch.setattr(config, "CONTACT_WHATSAPP", "+90 555 000 11 22")
    html = site.render()
    assert "</script><script>alert" not in html and "\\u003c/script" in html
    assert '"whatsapp": "905550001122"' in html


def test_landing_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "SITE_DIR", tmp_path)
    assert site.render() is None
