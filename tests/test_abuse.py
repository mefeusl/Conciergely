"""Usage tracking and the automatic freeze for exploitation patterns."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app import abuse, adminlogic, assistant, config, db, intake, jobs, pages, plans, scheduler, tools, whatsapp, worker, briefing
from app.models import Incoming
from conftest import FakeAnthropic, PHONE, response, text_block
from helpers import drain, say

BIG = "a" * 2600


@pytest.fixture(autouse=True)
def reset():
    yield
    assistant.set_client(None)


def send(text, mid, kind="text"):
    intake.intake([Incoming(id=mid, phone=PHONE, type=kind, text=text)])


def with_usage(text, i, o):
    r = response(text_block(text))
    r.usage = SimpleNamespace(input_tokens=i, output_tokens=o)
    return r


# ---------- usage tracking ----------

def test_messages_and_tokens_are_counted_per_client(client):
    assistant.set_client(FakeAnthropic(with_usage("Merhaba", 1200, 80), with_usage("Yine", 900, 40)))
    say("selam"); say("selam 2")
    use = plans.usage_table(PHONE)
    assert use["msgs"] == 2 and use["tokens_in"] == 2100 and use["tokens_out"] == 120
    assert abuse.tokens_last_hour(db.get_client(PHONE)) == 2220


def test_missing_or_garbage_usage_never_breaks_a_reply(client):
    junk = response(text_block("ok")); junk.usage = SimpleNamespace(input_tokens="many", output_tokens=None)
    assistant.set_client(FakeAnthropic(junk, response(text_block("ok2"))))
    assert say("a") == ["ok"] and say("b") == ["ok2"]
    assert plans.usage_table(PHONE).get("tokens_in", 0) == 0


def test_dropped_duplicates_are_not_counted(client):
    assistant.set_client(FakeAnthropic(response(text_block("x"))))
    send("selam", "dup"); send("selam", "dup")
    assert plans.used(PHONE, "msgs") == 1


# ---------- huge prompts ----------

def test_the_sixth_huge_prompt_in_an_hour_freezes_the_client(client):
    for i in range(5):
        send(BIG, f"h{i}")
    assert db.get_client(PHONE)["frozen"] == 0 and jobs.counts()["pending"] == 5
    send(BIG, "h5")
    c = db.get_client(PHONE)
    assert c["frozen"] == 1 and "6 huge prompts" in c["frozen_reason"] and c["frozen_at"]
    assert jobs.counts()["pending"] == 6  # five prompts + the notice; the sixth prompt was not queued


def test_the_client_is_told_to_call_the_owner(client, monkeypatch):
    monkeypatch.setattr(config, "OWNER_PHONE", "+90 555 000 11 22")
    for i in range(6):
        send(BIG, f"h{i}")
    with db.conn() as c:
        c.execute("DELETE FROM jobs WHERE kind='inbound'")
    drain()
    [notice] = [t for _, t in whatsapp.OUTBOX]
    assert "şüpheli" in notice and "+90 555 000 11 22" in notice and "öğleden sonra" in notice and "geri ararım" in notice


def test_notice_in_english_and_without_a_number(client, monkeypatch):
    no_number = abuse.notice_text({"lang": "en"})  # no number set: no phone call is promised
    assert "paused" in no_number and "call" not in no_number and "contact the person who set up" in no_number
    monkeypatch.setattr(config, "OWNER_PHONE", "+905550001122")
    assert "my personal number +905550001122" in abuse.notice_text({"lang": "en"})
    assert "+905550001122 numaralı" in abuse.notice_text({"lang": "tr"})
    assert "afternoon" in abuse.notice_text({"lang": "en"}) and "call you back" in abuse.notice_text({"lang": "en"})
    monkeypatch.setattr(config, "OWNER_PHONE", "")
    assert "numaralı" not in abuse.notice_text({"lang": "tr"})


def test_normal_long_messages_and_slow_pace_never_freeze(client):
    for i in range(30):
        send("b" * 2499, f"n{i}") if i < 20 else None  # just under the huge line
    assert db.get_client(PHONE)["frozen"] == 0


def test_old_huge_prompts_do_not_count(client):
    for i in range(5):
        send(BIG, f"o{i}")
    with db.conn() as c:
        c.execute("UPDATE jobs SET created_at=?", (db.to_iso(db.now_utc() - timedelta(hours=2)),))
    send(BIG, "new")
    assert db.get_client(PHONE)["frozen"] == 0


def test_huge_photo_captions_and_voice_are_not_text_prompts(client):
    for i in range(8):
        send(BIG, f"c{i}", kind="image")
    assert db.get_client(PHONE)["frozen"] == 0


def test_thresholds_can_be_changed_or_switched_off(client, monkeypatch):
    monkeypatch.setattr(config, "ABUSE_HUGE_PER_HOUR", 2)
    send(BIG, "a"); send(BIG, "b")
    assert db.get_client(PHONE)["frozen"] == 1
    abuse.unfreeze(PHONE)
    monkeypatch.setattr(config, "ABUSE_HUGE_PER_HOUR", 0)
    for i in range(10):
        send(BIG, f"z{i}")
    assert db.get_client(PHONE)["frozen"] == 0


# ---------- what a frozen client experiences ----------

def freeze_now():
    abuse.freeze(PHONE, "test")


def test_a_frozen_client_gets_one_notice_a_day_and_no_processing(client):
    fake = FakeAnthropic()
    assistant.set_client(fake)
    freeze_now()
    first = say("selam")
    assert len(first) == 1 and "şüpheli" in first[0]
    assert say("selam again") == [] and say("and again") == []  # same day: the dedup key holds
    assert fake.calls == [] and plans.used(PHONE, "msgs") == 0


def test_messages_already_queued_before_the_freeze_are_ignored(client):
    fake = FakeAnthropic()
    assistant.set_client(fake)
    send("selam", "q1")
    freeze_now()
    drain()
    assert fake.calls == [] and whatsapp.OUTBOX == []


def test_reminders_still_arrive_and_the_briefing_is_off(client):
    db.set_client_fields(PHONE, plan="plus", briefing_min=8 * 60 + 30)
    freeze_now()
    db.set_client_fields(PHONE, last_inbound_at=db.now_utc_iso())
    db.add_reminder(PHONE, "ilaç", db.to_iso(db.now_utc() - timedelta(minutes=1)))
    assert scheduler.tick_reminders() == 1
    drain()
    assert any("ilaç" in t for _, t in whatsapp.OUTBOX)
    from datetime import datetime, timezone
    assert briefing.tick(datetime(2026, 11, 2, 5, 30, tzinfo=timezone.utc)) == 0
    whatsapp.OUTBOX.clear()
    jobs.enqueue("briefing", PHONE, {"forced": True}); drain()
    assert whatsapp.OUTBOX == []


def test_unfreezing_restores_service_and_restarts_the_counters(client):
    for i in range(6):
        send(BIG, f"h{i}")
    assert db.get_client(PHONE)["frozen"] == 1
    abuse.unfreeze(PHONE)
    c = db.get_client(PHONE)
    assert c["frozen"] == 0 and c["frozen_reason"] == "" and c["abuse_reset_at"]
    assert abuse.huge_count(c) == 0
    # let a few seconds "pass": the unfreeze moment moves into the past, the old prompts further back
    with db.conn() as conn:
        conn.execute("UPDATE jobs SET created_at=?", (db.to_iso(db.now_utc() - timedelta(seconds=30)),))
    db.set_client_fields(PHONE, abuse_reset_at=db.to_iso(db.now_utc() - timedelta(seconds=10)))
    for i in range(5):
        send(BIG, f"again{i}")
    assert db.get_client(PHONE)["frozen"] == 0     # needs a whole new pattern
    send(BIG, "again5")
    assert db.get_client(PHONE)["frozen"] == 1


# ---------- token burn ----------

def test_an_extreme_hour_of_ai_use_freezes_after_the_reply(client, monkeypatch):
    monkeypatch.setattr(config, "ABUSE_TOKENS_PER_HOUR", 10_000)
    assistant.set_client(FakeAnthropic(with_usage("küçük", 3000, 100), with_usage("büyük", 9000, 500), response(text_block("x"))))
    assert say("bir") == ["küçük"] and db.get_client(PHONE)["frozen"] == 0
    out = say("iki")
    assert out[0] == "büyük" and "şüpheli" in out[1]  # the answer, then the notice
    c = db.get_client(PHONE)
    assert c["frozen"] == 1 and "12,600 AI tokens" in c["frozen_reason"]
    assert say("üç") == []  # nothing more is processed (today's notice was already sent)


def test_old_token_use_is_forgotten_and_zero_disables(client, monkeypatch):
    abuse.log_tokens(PHONE, 5_000_000)
    with db.conn() as c:
        c.execute("UPDATE token_log SET at=?", (db.to_iso(db.now_utc() - timedelta(hours=3)),))
    assert abuse.tokens_last_hour(db.get_client(PHONE)) == 0 and abuse.check_tokens(PHONE) is False
    abuse.log_tokens(PHONE, 5_000_000)
    monkeypatch.setattr(config, "ABUSE_TOKENS_PER_HOUR", 0)
    assert abuse.check_tokens(PHONE) is False
    monkeypatch.setattr(config, "ABUSE_TOKENS_PER_HOUR", 1_000_000)
    assert abuse.check_tokens(PHONE) is True and db.get_client(PHONE)["frozen"] == 1
    assert abuse.check_tokens(PHONE) is False  # already frozen: no second notice


def test_unfreeze_forgets_earlier_token_use(client):
    abuse.log_tokens(PHONE, 5_000_000)
    abuse.check_tokens(PHONE)
    abuse.unfreeze(PHONE)
    assert abuse.tokens_last_hour(db.get_client(PHONE)) == 0 and abuse.check_tokens(PHONE) is False


def test_nothing_is_limited_for_a_heavy_normal_user(client):
    assistant.set_client(FakeAnthropic(*[response(text_block("ok")) for _ in range(40)]))
    for i in range(40):
        assert say(f"mesaj {i}") == ["ok"]
    assert db.get_client(PHONE)["frozen"] == 0 and plans.used(PHONE, "msgs") == 40


def test_erasing_a_client_removes_the_token_log(client):
    abuse.log_tokens(PHONE, 10)
    db.erase_client(PHONE)
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM token_log").fetchone()[0] == 0


# ---------- admin ----------

def test_admin_freeze_and_unfreeze(client):
    assert adminlogic.set_frozen(client["id"], True) and db.get_client(PHONE)["frozen"] == 1
    assert "owner" in db.get_client(PHONE)["frozen_reason"]
    assert adminlogic.set_frozen(client["id"], False) and db.get_client(PHONE)["frozen"] == 0
    assert adminlogic.set_frozen(9999, True) is False


def test_admin_pages_show_usage_and_frozen_clients(client):
    plans.record(PHONE, "msgs", 12); plans.record(PHONE, "tokens_in", 2000); plans.record(PHONE, "tokens_out", 500)
    html = pages.render("dashboard", bot="A", csrf="t", **adminlogic.dashboard())
    assert ">12<" in html and ">2,500<" in html and "Frozen clients" not in html
    abuse.freeze(PHONE, "6 huge prompts <script>x</script>")
    html = pages.render("dashboard", bot="A", csrf="t", **adminlogic.dashboard())
    assert "Frozen clients" in html and "<script>x" not in html and "&lt;script&gt;" in html
    detail = pages.render("detail", bot="A", csrf="t", **adminlogic.detail(client["id"]))
    assert "FROZEN" in detail
    assert "Unfreeze" in detail and "tokens_in" in detail and "no limit" in detail and "Last hour" in detail
    abuse.unfreeze(PHONE)
    detail = pages.render("detail", bot="A", csrf="t", **adminlogic.detail(client["id"]))
    assert "Unfreeze" not in detail and "Freeze this client" in detail


def test_admin_hour_stats_count_what_happened(client):
    for i in range(3):
        send(BIG, f"s{i}")
    send("kısa", "s9")
    hour = adminlogic.detail(client["id"])["hour"]
    assert hour["messages"] == 4 and hour["huge"] == 3


def test_exactly_the_huge_line_counts_and_one_below_does_not(client, monkeypatch):
    monkeypatch.setattr(config, "ABUSE_HUGE_PER_HOUR", 2)
    send("a" * (config.ABUSE_HUGE_CHARS - 1), "x1"); send("a" * (config.ABUSE_HUGE_CHARS - 1), "x2")
    assert db.get_client(PHONE)["frozen"] == 0
    send("a" * config.ABUSE_HUGE_CHARS, "y1"); send("a" * config.ABUSE_HUGE_CHARS, "y2")
    assert db.get_client(PHONE)["frozen"] == 1


def test_token_line_is_inclusive(client, monkeypatch):
    monkeypatch.setattr(config, "ABUSE_TOKENS_PER_HOUR", 1000)
    abuse.log_tokens(PHONE, 999)
    assert abuse.check_tokens(PHONE) is False
    abuse.log_tokens(PHONE, 1)
    assert abuse.check_tokens(PHONE) is True
