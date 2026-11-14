"""End-to-end through the queue: incoming message -> intake -> worker -> Claude (scripted) -> outgoing message."""
import json

import pytest

from app import (assistant, config, conversation, db, i18n, intake, jobs, markets, memory, onboarding,
                 scheduler, voice, whatsapp, worker)
from app.models import Incoming

from conftest import FakeAnthropic, PHONE, response, text_block, tool_block


@pytest.fixture(autouse=True)
def reset_assistant():
    yield
    assistant.set_client(None)


@pytest.fixture
def new_client():
    """A registered client who has not started setup yet."""
    return db.upsert_client(PHONE, name="Mehmet")


def say(text, n=[0], phone=PHONE, kind="text", **extra):
    """Deliver one message through the full pipeline and return everything the bot sent back."""
    n[0] += 1
    whatsapp.OUTBOX.clear()
    intake.intake([Incoming(id=f"wamid.{n[0]}", phone=phone, type=kind, text=text, **extra)])
    while worker.process_one():
        pass
    return [t for (_, t) in whatsapp.OUTBOX]


def quote(symbol):
    return {"symbol": "THYAO.IS", "price": 305.0, "previous_close": 300.0, "change_pct": 1.6, "currency": "TRY"}


# ---------- onboarding ----------

def test_first_message_gets_greeting_and_question_one(new_client):
    [reply] = say("merhaba")
    assert "Merhaba, ben Conciergely" in reply and "1/5" in reply and "English" in reply and "geç" in reply
    assert db.get_client(PHONE)["onboarding_step"] == 1


def test_full_onboarding_saves_memory_and_sets_up_stocks(new_client, monkeypatch):
    monkeypatch.setattr(markets, "get_quote", quote)
    fake = FakeAnthropic(
        response(tool_block("t1", "watch_stock", symbol="THYAO", above=320, below=290), stop="tool_use"),
        response(text_block("THYAO için 320 üst, 290 alt limit kuruldu.")),
    )
    assistant.set_client(fake)
    say("selam")
    answers = ["Mehmet Bey, Türkçe", "Kısa ve resmi", "Hatırlatmalar ve piyasa takibi",
               "23:00 ile 08:00 arası yazma"]
    for number, answer in enumerate(answers, start=2):
        [reply] = say(answer)
        assert f"{number}/5" in reply
    reply = say("THYAO üst 320, alt 290")  # answer to the last question, the stock question
    assert "limit kuruldu" in reply[0] and "tanışmış olduk" in reply[0]
    assert [(w["symbol"], w["upper"], w["lower"]) for w in db.list_watch(PHONE)] == [("THYAO.IS", 320.0, 290.0)]
    assert db.get_client(PHONE)["onboarding_done"] == 1
    mem = memory.render(PHONE)
    assert "Mehmet Bey" in mem and "Kısa ve resmi" in mem and "23:00" in mem


def test_skip_a_question_and_skip_everything(new_client):
    say("merhaba")
    [reply] = say("Geç")
    assert "2/5" in reply and memory.count(PHONE) == 0
    [done] = say("kurulumu atla")
    assert "tanışmış olduk" in done and db.get_client(PHONE)["onboarding_done"] == 1


def test_first_message_can_skip_straight_away(new_client):
    [done] = say("Kurulumu atla")
    assert "tanışmış olduk" in done


def test_english_answer_switches_language(new_client):
    say("hi")
    [q2] = say("Call me Sam, English please")
    assert "2/5" in q2 and "How should I reply" in q2
    assert db.get_client(PHONE)["lang"] == "en"


def test_secrets_in_onboarding_answers_are_not_stored(new_client):
    say("merhaba")
    say("Mehmet")
    [reply] = say("kart numaram 4111 1111 1111 1111")
    assert "kaydetmiyorum" in reply and "4111" not in memory.render(PHONE)


def test_onboarding_with_no_stocks_makes_no_ai_call(new_client):
    fake = FakeAnthropic()  # any call would raise
    assistant.set_client(fake)
    db.set_client_fields(PHONE, onboarding_step=5)
    [reply] = say("geç")
    assert "tanışmış olduk" in reply and fake.calls == [] and db.get_client(PHONE)["onboarding_done"] == 1


# ---------- normal chat with tools ----------

def test_chat_with_tool_use_queues_reply_and_saves_history(client):
    due = (__import__("datetime").datetime.now() + __import__("datetime").timedelta(days=2)).strftime("%Y-%m-%d 09:00")
    fake = FakeAnthropic(
        response(tool_block("t1", "set_reminder", text="Ahmet'i ara", when_local=due), stop="tool_use"),
        response(text_block("Tamam, hatırlatma kuruldu.")),
    )
    assistant.set_client(fake)
    assert say("Ahmet'i yarından sonra 9'da aramamı hatırlat") == ["Tamam, hatırlatma kuruldu."]
    assert db.list_pending_reminders(PHONE)[0]["text"] == "Ahmet'i ara"
    # the tool result was handed back to Claude
    tool_result = fake.calls[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and json.loads(tool_result["content"])["ok"] is True
    assert [m["role"] for m in db.get_history(PHONE)] == ["user", "assistant"]


def test_memory_and_rules_reach_the_prompt_and_history_is_replayed(client):
    memory.add_fact(PHONE, "people", "Eşi Ayşe")
    fake = FakeAnthropic(response(text_block("Merhaba")), response(text_block("Yine merhaba")))
    assistant.set_client(fake)
    say("selam")
    say("tekrar selam")
    system = fake.calls[0]["system"]
    assert "Conciergely" in system and "[people] Eşi Ayşe" in system and "Never recommend buying" in system
    assert fake.calls[1]["messages"][:2] == [{"role": "user", "content": "selam"}, {"role": "assistant", "content": "Merhaba"}]
    assert fake.calls[1]["model"] == config.CLAUDE_MODEL and fake.calls[1]["tools"]


def test_claude_can_remember_through_a_tool(client):
    assistant.set_client(FakeAnthropic(
        response(tool_block("t1", "remember", section="preferences", text="Prefers short replies"), stop="tool_use"),
        response(text_block("Not aldım.")),
    ))
    say("kısa yanıtları severim")
    assert "Prefers short replies" in memory.render(PHONE)


def test_runaway_tool_loop_stops_with_a_fallback(client):
    loop = [response(tool_block(f"t{i}", "list_watchlist"), stop="tool_use") for i in range(assistant.MAX_STEPS)]
    fake = FakeAnthropic(*loop)
    assistant.set_client(fake)
    [reply] = say("hello")
    assert "yanıt veremiyorum" in reply and len(fake.calls) == assistant.MAX_STEPS


def test_api_failure_retries_and_never_replies_twice(client):
    class Flaky(FakeAnthropic):
        def _create(self, **kw):
            self.calls.append(kw)
            if len(self.calls) == 1:
                raise ConnectionError("api down")
            return response(text_block("Geri döndüm"))

    fake = Flaky()
    assistant.set_client(fake)
    intake.intake([Incoming(id="w1", phone=PHONE, text="selam")])
    assert worker.process_one() is True
    assert jobs.counts()["pending"] == 1 and whatsapp.OUTBOX == []
    with db.conn() as c:
        row = c.execute("SELECT attempts, last_error FROM jobs").fetchone()
        c.execute("UPDATE jobs SET run_at=?", (db.now_utc_iso(),))
    assert row["attempts"] == 1 and "ConnectionError" in row["last_error"]
    while worker.process_one():
        pass
    assert [t for _, t in whatsapp.OUTBOX] == ["Geri döndüm"]
    assert jobs.counts()["done"] == 2 and jobs.counts()["pending"] == 0


def test_processing_the_same_message_twice_queues_one_reply(client):
    assistant.set_client(FakeAnthropic(response(text_block("bir")), response(text_block("iki"))))
    job = {"phone": PHONE, "payload": {"id": "w9", "type": "text", "text": "selam"}}
    conversation.process_inbound(job)
    conversation.process_inbound(job)
    assert jobs.counts()["pending"] == 1


# ---------- erase command ----------

def test_erase_needs_confirmation(client):
    memory.add_fact(PHONE, "notes", "Something to remember")
    [ask] = say("Hepsini unut")
    assert "EVET" in ask and memory.count(PHONE) == 1
    assert say("evet") == [i18n.t("tr", "erase_done")] and memory.count(PHONE) == 0


def test_erase_can_be_cancelled(client):
    memory.add_fact(PHONE, "notes", "Something to remember")
    say("forget everything")
    assert "iptal" in say("hayır")[0] and memory.count(PHONE) == 1
    assert db.get_client(PHONE)["pending_action"] == ""


# ---------- stop command ----------

@pytest.mark.parametrize("word", ["dur", "STOP", "Stop!", "bildirimleri kapat", "unsubscribe"])
def test_stop_turns_off_automatic_messages(client, word):
    db.set_client_fields(PHONE, briefing_min=480, news_alerts=1)
    assert say(word) == [i18n.t("tr", "stop_done")]
    c = db.get_client(PHONE)
    assert c["briefing_min"] == -1 and c["news_alerts"] == 0


def test_stop_inside_a_sentence_is_not_the_command(client):
    db.set_client_fields(PHONE, briefing_min=480, news_alerts=1)
    say("THYAO için stop 300 olsun")
    c = db.get_client(PHONE)
    assert c["briefing_min"] == 480 and c["news_alerts"] == 1


def test_greeting_mentions_storage_and_stop():
    for lang in ("tr", "en"):
        text = i18n.t(lang, "greeting")
        assert "dur" in text or "stop" in text


# ---------- voice notes ----------

def test_voice_note_without_openai_key_asks_to_type(client):
    [reply] = say("", kind="audio", media_id="123", mime="audio/ogg")
    assert "yazarak" in reply


def test_voice_note_is_transcribed_then_answered(client, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "key")
    monkeypatch.setattr(whatsapp, "download_media", lambda ref: (b"audio", "audio/ogg"))
    monkeypatch.setattr(voice, "transcribe", lambda audio, mime, client=None: "yarın hava nasıl")
    fake = FakeAnthropic(response(text_block("Bakıyorum.")))
    assistant.set_client(fake)
    [reply] = say("", kind="audio", media_id="123", mime="audio/ogg")
    assert reply == "Duyduğum: “yarın hava nasıl”\n\nBakıyorum."  # the transcript is echoed back
    assert fake.calls[0]["messages"][-1] == {"role": "user", "content": "yarın hava nasıl"}


def test_unintelligible_voice_note_and_other_media(client, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "key")
    monkeypatch.setattr(whatsapp, "download_media", lambda ref: (b"audio", "audio/ogg"))
    monkeypatch.setattr(voice, "transcribe", lambda audio, mime, client=None: None)
    assert "anlayamadım" in say("", kind="audio", media_id="1")[0]
    assert "fotoğraf ve PDF" in say("", kind="other")[0]


# ---------- registration & delivery rules ----------

def test_unregistered_and_paused_numbers_get_no_reply():
    stranger = Incoming(id="s1", phone="+905550000000", text="selam")
    assert intake.intake([stranger]) == 0  # never queued as a conversation; they only get the one canned referral reply
    assert db.get_client("+905550000000") is None
    assert jobs.counts()["pending"] == 1
    while worker.process_one():  # deliver the stranger's canned reply so it does not mix into the next check
        pass
    db.upsert_client(PHONE, name="X", active=0)
    assert intake.intake([Incoming(id="s2", phone=PHONE, text="selam")]) == 0
    assert say("selam") == []  # a deactivated client is ignored (the stranger reply above is a different number)


def test_worker_also_refuses_a_job_for_a_client_removed_after_queueing(client):
    jobs.enqueue("inbound", PHONE, {"id": "late", "type": "text", "text": "selam"})
    db.set_client_fields(PHONE, active=0)
    worker.process_one()
    assert jobs.counts()["pending"] == 0 and whatsapp.OUTBOX == []


def test_intake_rate_limit_dedup_and_window(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_INBOUND_PER_HOUR", 3)
    msgs = [Incoming(id=f"r{i}", phone=PHONE, text="x") for i in range(5)]
    assert intake.intake(msgs) == 3
    assert intake.intake([Incoming(id="r0", phone=PHONE, text="x")]) == 0  # duplicate id / over limit
    assert db.window_open(PHONE) is True


def test_proactive_messages_use_template_when_window_closed(client, monkeypatch):
    monkeypatch.setattr(config, "META_TEMPLATE_NAME", "alert_v1")
    jobs.enqueue("send", PHONE, {"text": "THYAO limit aşıldı", "proactive": True})
    worker.process_one()
    assert whatsapp.OUTBOX == [(PHONE, "[TEMPLATE] THYAO limit aşıldı")]


def test_proactive_message_inside_window_is_plain_text(client):
    db.set_client_fields(PHONE, last_inbound_at=db.now_utc_iso())
    jobs.enqueue("send", PHONE, {"text": "Hatırlatma: su iç", "proactive": True})
    worker.process_one()
    assert whatsapp.OUTBOX == [(PHONE, "Hatırlatma: su iç")]


def test_closed_window_without_template_goes_dead_with_a_clear_reason(client):
    jobs.enqueue("send", PHONE, {"text": "alert", "proactive": True})
    worker.process_one()
    [dead] = jobs.list_jobs("dead")
    assert "META_TEMPLATE_NAME" in dead["last_error"] and whatsapp.OUTBOX == []


def test_admin_test_ping_uses_hello_template_when_window_closed(client):
    jobs.enqueue("send", PHONE, {"hello": True})
    worker.process_one()
    assert whatsapp.OUTBOX == [(PHONE, "[HELLO TEMPLATE]")]
    whatsapp.OUTBOX.clear()
    db.set_client_fields(PHONE, last_inbound_at=db.now_utc_iso())
    jobs.enqueue("send", PHONE, {"hello": True})
    worker.process_one()
    assert "çalışıyor" in whatsapp.OUTBOX[0][1]


# ---------- reminders ----------

def test_due_reminder_is_sent_exactly_once(client):
    db.add_reminder(PHONE, "İlacı al", "2000-01-01T00:00:00")
    db.add_reminder(PHONE, "Gelecek", "2999-01-01T00:00:00")
    assert scheduler.tick_reminders() == 1
    assert scheduler.tick_reminders() == 0
    job = jobs.claim()
    assert job["payload"] == {"text": "Hatırlatma: İlacı al", "proactive": True}
    assert jobs.claim() is None
    assert len(db.list_pending_reminders(PHONE)) == 1  # only the future one is still pending


def test_crash_between_queueing_and_marking_does_not_duplicate_a_reminder(client, monkeypatch):
    db.add_reminder(PHONE, "İlacı al", "2000-01-01T00:00:00")
    real_mark = db.mark_reminder

    def crash(*a, **k):
        raise RuntimeError("power cut")

    monkeypatch.setattr(db, "mark_reminder", crash)
    with pytest.raises(RuntimeError):
        scheduler.tick_reminders()  # message queued, but the reminder was never marked as sent
    monkeypatch.setattr(db, "mark_reminder", real_mark)
    scheduler.tick_reminders()  # the next tick finds it still due...
    assert jobs.counts()["pending"] == 1  # ...but the dedup key stops a second copy
    assert db.list_pending_reminders(PHONE) == []


def test_reminder_for_paused_client_is_dropped(client):
    db.add_reminder(PHONE, "x y", "2000-01-01T00:00:00")
    db.set_client_fields(PHONE, active=0)
    assert scheduler.tick_reminders() == 0 and jobs.counts()["pending"] == 0
    assert db.list_pending_reminders(PHONE) == []


# ---------- fixed texts ----------

def test_both_languages_have_the_same_messages_and_questions():
    assert set(i18n.TEXTS["tr"]) == set(i18n.TEXTS["en"])
    assert i18n.TOTAL_QUESTIONS == 5
    for q in i18n.QUESTIONS:
        assert q["tr"] and q["en"] and q["section"] in memory.SECTIONS
    assert i18n.clean("  GEÇ! ") in i18n.SKIP and i18n.clean("Hepsini UNUT.") in i18n.ERASE
