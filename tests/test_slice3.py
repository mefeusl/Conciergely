"""Slice 3: forwarded messages and photos offer reminders, Sunday recap, heads-up day, first-day message."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import adminlogic, assistant, config, conversation, db, files, jobs, lifecycle, whatsapp, wa_meta, worker

PHONE = "+905551234567"
TZ = ZoneInfo("Europe/Istanbul")


def _client(plan="basic", **fields):
    adminlogic.add_client(PHONE, "Ali Veli", plan)
    db.set_client_fields(PHONE, onboarding_done=1, **fields)
    return db.get_client(PHONE)


def _sends(kind_prefix=""):
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM jobs WHERE kind='send'")]


def _at(day, hour):
    return datetime(2026, 11, day, hour, 5, tzinfo=TZ).astimezone(timezone.utc)  # 2026-11-01 is a Sunday


def test_meta_marks_forwarded_messages():
    body = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"messages": [
        {"id": "w1", "from": "905551234567", "type": "text", "text": {"body": "randevu 10:30"}, "context": {"forwarded": True}},
        {"id": "w2", "from": "905551234567", "type": "text", "text": {"body": "selam"}}]}}]}]}
    a, b = wa_meta.parse_payload(body)
    assert a.forwarded and not b.forwarded


def test_forwarded_text_gets_the_forward_instruction(monkeypatch):
    c = _client()
    prep = conversation._prepare({"type": "text", "text": "Randevunuz 14 Kasım 10:30", "forwarded": True}, "tr", c)
    assert prep.extra == files.FORWARD_INSTRUCTION and "reminder" in prep.extra and "wait for a yes" in prep.extra
    assert conversation._prepare({"type": "text", "text": "selam"}, "tr", c).extra is None or conversation._prepare({"type": "text", "text": "selam"}, "tr", c).extra == ""


def test_photo_and_document_instructions_offer_a_reminder_but_ask_first():
    for instr in (files.IMAGE_INSTRUCTION, files.DOCUMENT_INSTRUCTION):
        assert "Do NOT call set_reminder until the client says yes" in instr


def test_sunday_recap_lists_next_week_and_only_once():
    c = _client()
    due = lambda d, h: db.to_iso(datetime(2026, 11, d, h, 0, tzinfo=TZ).astimezone(timezone.utc))
    db.add_reminder(PHONE, "Toplantı", due(3, 10))
    db.add_reminder(PHONE, "Dişçi", due(5, 14))
    db.add_reminder(PHONE, "Çok sonra", due(20, 14))
    assert lifecycle.tick(_at(1, 19)) == 1
    [job] = [j for j in _sends() if "recap" in (j["dedup_key"] or "")]
    text = json.loads(job["payload"])["text"]
    assert "2 hatırlatmanız" in text and "Toplantı" in text and "Dişçi" in text and "Çok sonra" not in text
    assert lifecycle.tick(_at(1, 19)) == 0  # not twice the same day


def test_no_reminders_means_no_message_and_lite_gets_none():
    _client()
    assert lifecycle.tick(_at(1, 19)) == 0
    db.set_client_fields(PHONE, plan="lite", recap_last="")
    db.add_reminder(PHONE, "x", db.to_iso(_at(3, 10)))
    assert lifecycle.tick(_at(1, 19)) == 0


def test_heads_up_needs_two_reminders_tomorrow_and_skips_sunday():
    _client()
    due = lambda d, h: db.to_iso(datetime(2026, 11, d, h, 0, tzinfo=TZ).astimezone(timezone.utc))
    db.add_reminder(PHONE, "Bir", due(3, 9))
    assert lifecycle.tick(_at(2, 20)) == 0  # Monday evening, only one reminder tomorrow
    db.add_reminder(PHONE, "İki", due(3, 15))
    assert lifecycle.tick(_at(2, 20)) == 1
    text = json.loads([j for j in _sends() if "headsup" in (j["dedup_key"] or "")][0]["payload"])["text"]
    assert text.startswith("Yarın için 2 hatırlatmanız")
    db.set_client_fields(PHONE, recap_last="")
    assert lifecycle.tick(_at(1, 20)) == 0  # Sunday 20:00: the recap hour was 19


def test_stop_and_commands_turn_the_recap_off_and_on():
    c = _client()
    assert "kapalı" in lifecycle.handle(c, "ozet kapat")
    assert db.get_client(PHONE)["recap_on"] == 0
    db.add_reminder(PHONE, "x", db.to_iso(_at(3, 10)))
    assert lifecycle.tick(_at(1, 19)) == 0
    assert "açık" in lifecycle.handle(c, "ozet ac") and db.get_client(PHONE)["recap_on"] == 1
    assert lifecycle.handle(c, "merhaba") is None


def test_first_day_message_is_queued_by_setup_and_sent_once(monkeypatch):
    monkeypatch.setattr(config, "WOW_ON", True)
    c = _client()
    from app import onboarding
    db.set_client_fields(PHONE, onboarding_done=0)
    onboarding._finish(PHONE, 5)
    assert jobs.counts()["pending"] >= 1
    sent = []
    monkeypatch.setattr(assistant, "write", lambda system, prompt, max_tokens=300: 'Hazırız Ali!\n- "Yarın 10\'da hatırlat"\n- "Hava nasıl"')
    monkeypatch.setattr(whatsapp, "send_text", lambda phone, text: sent.append(text))
    job = {"phone": PHONE, "payload": {}}
    lifecycle.deliver_wow(job)
    lifecycle.deliver_wow(job)
    assert len(sent) == 1 and "Hazırız Ali" in sent[0]


def test_first_day_message_falls_back_to_fixed_text(monkeypatch):
    _client("lite")
    sent = []
    monkeypatch.setattr(assistant, "write", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(whatsapp, "send_text", lambda phone, text: sent.append(text))
    lifecycle.deliver_wow({"phone": PHONE, "payload": {}})
    assert "Ali" in sent[0] and "hatırlat" not in sent[0].lower()  # a Lite client is never offered reminders


def test_worker_routes_the_wow_job(monkeypatch):
    called = []
    monkeypatch.setattr(lifecycle, "deliver_wow", lambda job: called.append(job["kind"]))
    worker.handle({"kind": "wow", "phone": PHONE, "payload": {}, "id": 1})
    assert called == ["wow"]
