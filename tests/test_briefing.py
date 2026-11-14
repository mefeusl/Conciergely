"""The Plus audio morning briefing: scheduling, content, delivery and every way it can degrade."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

from app import (assistant, briefing, config, db, jobs, plans, quiet, tools, tts, wa_meta, wa_twilio, whatsapp,
                 worker)
from app.errors import Deferred, PermanentSendError
from conftest import FakeAnthropic, PHONE, response, text_block
from helpers import drain, open_window, say

UTC = timezone.utc
# 2026-11-02 is a Monday; Istanbul is UTC+3 all year, so 08:30 local = 05:30 UTC.
DAY = datetime(2026, 11, 2, tzinfo=UTC)


def at(local_hour, local_min=0, day=DAY):
    return day.replace(hour=local_hour, minute=local_min) - timedelta(hours=3)


PULSE = {"items": [{"name": "USD/TRY", "price": 34.1234, "change_pct": 0.2}, {"name": "BIST 100", "price": 10432.5, "change_pct": -0.8},
                   {"name": "VIX (fear gauge)", "price": 18.0, "change_pct": 1.0}, {"name": "Gold", "price": 2650.0, "change_pct": None}],
         "unavailable": []}


@pytest.fixture(autouse=True)
def reset():
    yield
    assistant.set_client(None)


@pytest.fixture
def plus(client):
    db.set_client_fields(PHONE, plan="plus", briefing_min=8 * 60 + 30, name="Mehmet")
    return db.get_client(PHONE)


@pytest.fixture
def fake_quote(monkeypatch):
    monkeypatch.setattr(briefing, "pulse", SimpleNamespace(snapshot=lambda: PULSE))
    return lambda sym: {"THYAO.IS": {"price": 305.456, "currency": "TRY", "change_pct": 1.234}}.get(sym)


# ---------- scheduling ----------

def test_tick_queues_once_at_the_chosen_time(plus):
    assert briefing.tick(at(8, 29)) == 0
    assert briefing.tick(at(8, 30)) == 1
    assert briefing.tick(at(8, 31)) == 0  # already queued today
    assert db.get_client(PHONE)["briefing_last"] == "2026-11-02"
    job = jobs.claim()
    assert job["kind"] == "briefing" and job["phone"] == PHONE and job["payload"] == {"date": "2026-11-02"}
    with db.conn() as c:  # even with the dedup key gone, briefing_last alone stops a second one
        c.execute("DELETE FROM jobs")
    assert briefing.tick(at(8, 32)) == 0
    assert briefing.tick(at(8, 30, DAY + timedelta(days=1))) == 1  # next day it comes again


def test_tick_catches_up_inside_the_grace_period_only(plus):
    assert briefing.tick(at(11, 29)) == 1   # 2 h 59 min late (grace 180)
    tomorrow = DAY + timedelta(days=1)  # a fresh day, so the dedup key cannot hide the result
    assert briefing.tick(at(11, 30, tomorrow)) == 0   # 3 h late: skipped for that day
    assert briefing.tick(at(23, 59, tomorrow)) == 0
    assert briefing.tick(at(8, 30, tomorrow)) == 1


def test_a_dedup_key_stops_a_second_job_even_if_briefing_last_is_lost(plus):
    briefing.tick(at(8, 30))
    db.set_client_fields(PHONE, briefing_last="")
    assert briefing.tick(at(8, 40)) == 0 and jobs.counts()["pending"] == 1


@pytest.mark.parametrize("change", [{"plan": "basic"}, {"active": 0}, {"briefing_min": -1}])
def test_tick_skips_basic_paused_and_opted_out_clients(plus, change):
    db.set_client_fields(PHONE, **change)
    assert briefing.tick(at(8, 30)) == 0 and jobs.counts()["pending"] == 0


def test_tick_uses_each_clients_own_time(plus):
    db.upsert_client("+905550000002", name="B")
    db.set_client_fields("+905550000002", plan="plus", briefing_min=7 * 60)
    assert briefing.tick(at(7, 0)) == 1
    assert briefing.tick(at(8, 30)) == 1


def test_tick_follows_the_local_date_not_utc(plus):
    db.set_client_fields(PHONE, briefing_min=30)  # 00:30 local = 21:30 UTC the evening before
    assert briefing.tick(datetime(2026, 11, 1, 21, 30, tzinfo=UTC)) == 1
    assert db.get_client(PHONE)["briefing_last"] == "2026-11-02"


# ---------- gathering ----------

def test_gather_collects_markets_stocks_and_todays_reminders(plus, fake_quote):
    db.add_watch(PHONE, "THYAO.IS", 320, 290, None)
    db.add_watch(PHONE, "GONE.IS", None, None, None)  # no quote: simply left out
    db.add_reminder(PHONE, "Toplantı", db.to_iso(at(14, 0)))
    db.add_reminder(PHONE, "Yarın", db.to_iso(at(9, 0, DAY + timedelta(days=1))))
    db.add_reminder(PHONE, "Heads-up", db.to_iso(at(13, 30)), kind="before", lead_minutes=30)
    cancelled = db.add_reminder(PHONE, "Iptal", db.to_iso(at(15, 0)))
    db.cancel_reminder(PHONE, cancelled)
    data = briefing.gather(plus, at(8, 30), quote_fn=fake_quote)
    assert [m["name"] for m in data["markets"]] == ["USD/TRY", "BIST 100", "Gold"]  # VIX is not a headline item
    assert data["stocks"] == [{"symbol": "THYAO.IS", "price": 305.46, "currency": "TRY", "change_pct": 1.23}]
    assert data["reminders"] == [{"time": "14:00", "text": "Toplantı"}]
    assert data["date"] == "2 Kasım Pazartesi"  # Turkish client: a Turkish date, never an English weekday


def test_gather_survives_every_source_failing(plus):
    db.add_watch(PHONE, "THYAO.IS", None, None, None)

    def boom(*a, **k):
        raise RuntimeError("yahoo down")
    data = briefing.gather(plus, at(8, 30), quote_fn=boom, pulse_fn=boom)
    assert data["markets"] == [] and data["stocks"] == [] and data["reminders"] == []


def test_gather_limits_the_number_of_stocks_and_reminders(plus, fake_quote):
    for i in range(12):
        db.add_watch(PHONE, f"S{i}.IS", None, None, None)
        db.add_reminder(PHONE, f"r{i}", db.to_iso(at(20, i)))
    data = briefing.gather(plus, at(8, 30), quote_fn=lambda s: {"price": 1.0, "currency": "TRY", "change_pct": 0})
    assert len(data["stocks"]) == briefing.MAX_STOCKS and len(data["reminders"]) == briefing.MAX_REMINDERS


# ---------- text and script ----------

DATA = {"date": "Monday 02 November", "markets": [{"name": "USD/TRY", "price": 34.1234, "change_pct": 0.2},
                                                   {"name": "BIST 100", "price": 10432.5, "change_pct": -0.8}],
        "stocks": [{"symbol": "THYAO.IS", "price": 305.46, "currency": "TRY", "change_pct": 1.2}],
        "reminders": [{"time": "14:00", "text": "Toplantı"}]}


def test_text_version_turkish(plus):
    text = briefing.text_version(plus, DATA)
    assert text.startswith("Günaydın Mehmet! İşte günün özeti.\nMonday 02 November")
    assert "- USD/TRY: 34.12 (+0.2%)" in text and "- BIST 100: 10,432 (-0.8%)" in text or "10,433" in text
    assert "- THYAO.IS: 305.46 TRY (+1.2%)" in text and "- 14:00 Toplantı" in text
    assert "15 dk gecikmeli" in text and text.endswith("Sesli dinlemek için 'brifing' yazın.")
    assert "Sesli dinlemek" not in briefing.text_version(plus, DATA, footer=False)


def test_text_version_english_and_empty_sections():
    c = {"lang": "en", "name": ""}
    text = briefing.text_version(c, {"date": "Monday", "markets": [], "stocks": [], "reminders": []})
    assert text.startswith("Good morning! Here is your day.") and "No reminders for today." in text
    assert "Markets:" not in text and "Your stocks:" not in text and "delayed about 15 minutes" in text
    assert text.endswith("Reply 'briefing' to hear it as audio.")


def test_script_uses_the_model_text(plus, monkeypatch):
    seen = {}
    monkeypatch.setattr(assistant, "write", lambda system, prompt: (seen.update(system=system, prompt=prompt),
                                                                    "Günaydın Mehmet,\n dolar 34 lira.")[1])
    assert briefing.script(plus, DATA) == "Günaydın Mehmet, dolar 34 lira."
    assert "NEVER say what to buy" in seen["system"] and "Language: Turkish" in seen["prompt"] and "Toplantı" in seen["prompt"]


def test_script_falls_back_to_the_plain_text(plus, monkeypatch):
    def boom(system, prompt):
        raise RuntimeError("api down")
    monkeypatch.setattr(assistant, "write", boom)
    out = briefing.script(plus, DATA)
    assert "dolar kuru" in out and "Toplantı" in out and "\n" not in out and "Sesli dinlemek" not in out
    monkeypatch.setattr(assistant, "write", lambda s, p: "")
    assert "dolar kuru" in briefing.script(plus, DATA)
    monkeypatch.setattr(assistant, "write", lambda s, p: "x " * 600)  # absurdly long
    assert "dolar kuru" in briefing.script(plus, DATA)
    assert len(briefing.script(plus, {**DATA, "reminders": [{"time": "1", "text": "a" * 100}] * 8})) <= tts.MAX_CHARS


# ---------- delivery ----------

@pytest.fixture
def ready(plus, fake_quote, monkeypatch):
    open_window()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "key")
    monkeypatch.setattr(briefing.pulse, "snapshot", lambda: PULSE)
    monkeypatch.setattr(briefing.markets, "get_quote", fake_quote)
    monkeypatch.setattr(assistant, "write", lambda system, prompt: "Günaydın, işte özet.")
    monkeypatch.setattr(tts, "speak", lambda text, **kw: b"OGG")
    return plus


def run_briefing(payload=None):
    whatsapp.OUTBOX.clear()
    jobs.enqueue("briefing", PHONE, payload or {})
    assert worker.process_one() is True
    return [t for (_, t) in whatsapp.OUTBOX]


def test_text_then_voice_note(ready):
    out = run_briefing()
    assert len(out) == 2 and out[0].startswith("Günaydın Mehmet!") and out[1] == "[VOICE 3 bytes]"
    assert "Sesli dinlemek" not in out[0]  # the hint is pointless when the audio follows
    assert plans.used(PHONE, "briefing") == 1 and jobs.counts()["done"] == 1


def test_the_voice_is_given_the_spoken_script(ready, monkeypatch):
    spoken = []
    monkeypatch.setattr(tts, "speak", lambda text, **kw: (spoken.append(text), b"x")[1])
    run_briefing()
    assert spoken == ["Günaydın, işte özet."]


def test_without_a_tts_key_only_text_goes_out_with_the_hint(ready, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    out = run_briefing()
    assert len(out) == 1 and out[0].endswith("'brifing' yazın.")


@pytest.mark.parametrize("failure", [RuntimeError("tts down"), requests.ConnectionError("x"), PermanentSendError("upload refused")])
def test_a_failed_voice_note_never_loses_the_text_or_retries(ready, monkeypatch, failure):
    def boom(*a, **k):
        raise failure
    monkeypatch.setattr(tts, "speak" if not isinstance(failure, PermanentSendError) else "speak", boom if not isinstance(failure, PermanentSendError) else (lambda t: b"x"))
    if isinstance(failure, PermanentSendError):
        monkeypatch.setattr(whatsapp, "send_voice", boom)
    out = run_briefing()
    assert len(out) == 1 and out[0].startswith("Günaydın") and jobs.counts()["done"] == 1 and jobs.counts()["dead"] == 0


def test_tts_returning_nothing_sends_text_only(ready, monkeypatch):
    monkeypatch.setattr(tts, "speak", lambda text, **kw: None)
    assert len(run_briefing()) == 1


def test_a_failing_text_send_retries_and_does_not_charge(ready, monkeypatch):
    def fail(phone, body):
        raise RuntimeError("meta 500")
    monkeypatch.setattr(whatsapp, "send_text", fail)
    jobs.enqueue("briefing", PHONE, {})
    worker.process_one()
    assert jobs.counts()["pending"] == 1 and plans.used(PHONE, "briefing") == 0


def test_outside_the_24h_window_the_text_goes_through_the_template(ready, monkeypatch):
    db.set_client_fields(PHONE, last_inbound_at=db.to_iso(db.now_utc() - timedelta(hours=30)))
    monkeypatch.setattr(config, "META_TEMPLATE_NAME", "daily_update")
    out = run_briefing()
    assert len(out) == 1 and out[0].startswith("[TEMPLATE] Günaydın Mehmet!") and plans.used(PHONE, "briefing") == 1


def test_outside_the_window_without_a_template_it_is_skipped_quietly(ready):
    db.set_client_fields(PHONE, last_inbound_at=db.to_iso(db.now_utc() - timedelta(hours=30)))
    assert run_briefing() == [] and jobs.counts()["dead"] == 0 and plans.used(PHONE, "briefing") == 0


def test_quiet_hours_push_it_back_but_a_requested_one_goes_now(ready, monkeypatch):
    monkeypatch.setattr(quiet, "in_quiet", lambda c, now=None: True)
    monkeypatch.setattr(quiet, "next_end", lambda c, now=None: db.now_utc() + timedelta(hours=1))
    assert run_briefing() == [] and jobs.counts()["pending"] == 1
    with pytest.raises(Deferred):
        briefing.deliver({"phone": PHONE, "payload": {}})
    with db.conn() as c:
        c.execute("DELETE FROM jobs")
    assert len(run_briefing({"forced": True})) == 2


def test_monthly_cap(ready, monkeypatch):
    monkeypatch.setattr(config, "PLAN_LIMITS_JSON", '{"plus": {"briefing": 1}}')
    assert len(run_briefing()) == 2
    assert run_briefing() == []                          # scheduled one: silently dropped
    out = run_briefing({"forced": True})                 # asked for: told why
    assert len(out) == 1 and "hakkınız doldu" in out[0] and "sesli brifing" in out[0]
    assert plans.used(PHONE, "briefing") == 1


@pytest.mark.parametrize("change", [{"plan": "basic"}, {"active": 0}])
def test_a_downgraded_or_paused_client_gets_nothing(ready, change):
    db.set_client_fields(PHONE, **change)
    assert run_briefing() == [] and jobs.counts()["dead"] == 0


def test_deleted_client_is_ignored(ready):
    db.erase_client(PHONE)
    jobs.enqueue("briefing", PHONE, {})
    assert worker.process_one() and whatsapp.OUTBOX == []


def test_the_scheduler_to_worker_path(ready, monkeypatch):
    assert briefing.tick(at(8, 30)) == 1
    assert worker.process_one()
    assert len(whatsapp.OUTBOX) == 2


# ---------- asking for it ----------

@pytest.mark.parametrize("word", ["brifing", "Briefing", "Sesli brifing!", "morning briefing", "BRİFİNG"])
def test_the_command_word_sends_it_now(ready, word):
    out = say(word)
    assert out[0] == "Brifingi hazırlıyorum, birkaç saniye sürer." and len(out) == 3
    assert out[1].startswith("Günaydın") and out[2].startswith("[VOICE")


def test_command_on_basic_explains_it_is_plus(basic_client):
    assert say("brifing") == ["Sesli sabah brifingi Plus paketine dahil."] and jobs.counts()["pending"] == 0


def test_the_same_message_delivered_twice_makes_one_briefing(ready):
    from app.models import Incoming
    from app import intake
    for _ in range(2):
        intake.intake([Incoming(id="dup1", phone=PHONE, text="brifing")])
    drain()
    assert len([1 for _, t in whatsapp.OUTBOX if t.startswith("[VOICE")]) == 1


def test_claude_can_send_one_on_request(ready):
    fake = FakeAnthropic(
        response(SimpleNamespace(type="tool_use", id="b1", name="send_briefing_now", input={}), stop="tool_use"),
        response(text_block("Geliyor.")))
    assistant.set_client(fake)
    out = say("bana brifing gönderir misin lütfen")
    assert out[0] == "Geliyor." and any(t.startswith("[VOICE") for t in out)
    def briefing_jobs():
        with db.conn() as c:
            return c.execute("SELECT COUNT(*) FROM jobs WHERE kind='briefing'").fetchone()[0]
    before = briefing_jobs()
    assert json.loads(tools.run(PHONE, "send_briefing_now", {}))["ok"] is True
    assert json.loads(tools.run(PHONE, "send_briefing_now", {}))["ok"] is True
    assert briefing_jobs() - before <= 1  # two calls within one minute never make two briefings


def test_set_briefing_tool(plus):
    assert json.loads(tools.run(PHONE, "set_briefing", {"time": "07:15"}))["briefing"] == "07:15"
    assert db.get_client(PHONE)["briefing_min"] == 435
    for bad in ({"time": "7.15"}, {"time": "25:00"}, {"time": ""}, {}):
        assert "error" in json.loads(tools.run(PHONE, "set_briefing", bad))
    assert db.get_client(PHONE)["briefing_min"] == 435
    assert json.loads(tools.run(PHONE, "set_briefing", {"off": True}))["briefing"] == "off"
    assert db.get_client(PHONE)["briefing_min"] == -1


# ---------- text to speech ----------

def fake_post(monkeypatch, content=b"OggS-audio", status_ok=True):
    sent = {}

    def post(url, headers=None, json=None, timeout=None, **kw):
        sent.update(url=url, headers=headers, json=json, timeout=timeout)

        def raise_for_status():
            if not status_ok:
                raise requests.HTTPError("500")
        return SimpleNamespace(content=content, raise_for_status=raise_for_status)
    monkeypatch.setattr(tts.requests, "post", post)
    return sent


def test_speak_asks_for_opus(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    sent = fake_post(monkeypatch)
    assert tts.speak("  Günaydın   dünya \n") == b"OggS-audio"
    assert sent["url"].endswith("/audio/speech") and sent["headers"]["Authorization"] == "Bearer sk-test"
    assert sent["json"]["response_format"] == "opus" and sent["json"]["input"] == "Günaydın dünya"
    assert sent["json"]["model"] == config.TTS_MODEL and sent["json"]["voice"] == config.TTS_VOICE


def test_speak_edge_cases(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    assert tts.speak("hi") is None and not tts.available()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "k")
    sent = fake_post(monkeypatch)
    assert tts.speak("") is None and tts.speak("   ") is None and sent == {}
    tts.speak("a" * 5000)
    assert len(sent["json"]["input"]) == tts.MAX_CHARS
    fake_post(monkeypatch, content=b"")
    assert tts.speak("hi") is None
    fake_post(monkeypatch, content=b"x" * (tts.MAX_BYTES + 1))
    assert tts.speak("hi") is None
    fake_post(monkeypatch, status_ok=False)
    with pytest.raises(requests.HTTPError):
        tts.speak("hi")


# ---------- sending voice through the providers ----------

def test_meta_voice_uploads_then_sends(monkeypatch):
    calls = []

    def post(url, headers=None, json=None, data=None, files=None, timeout=None, **kw):
        calls.append((url, json, data, files))
        return SimpleNamespace(status_code=200, json=lambda: {"id": "MEDIA9"})
    monkeypatch.setattr(wa_meta.requests, "post", post)
    wa_meta.send_voice("+905551234567", b"OGGDATA")
    (url1, _, data1, files1), (url2, body2, _, _) = calls
    assert url1.endswith("/123456789/media") and data1["type"] == "audio/ogg" and files1["file"][1] == b"OGGDATA"
    assert files1["file"][2] == "audio/ogg"
    assert url2.endswith("/123456789/messages")
    assert body2 == {"messaging_product": "whatsapp", "to": "905551234567", "type": "audio", "audio": {"id": "MEDIA9"}}


def test_meta_voice_failures(monkeypatch):
    monkeypatch.setattr(wa_meta.requests, "post", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {}))
    with pytest.raises(PermanentSendError):
        wa_meta.send_voice("+905551234567", b"x")
    monkeypatch.setattr(wa_meta.requests, "post", lambda *a, **k: SimpleNamespace(status_code=400, json=lambda: {"error": {"code": 100, "message": "bad"}}))
    with pytest.raises(PermanentSendError):
        wa_meta.send_voice("+905551234567", b"x")
    monkeypatch.setattr(config, "META_ACCESS_TOKEN", "")
    with pytest.raises(PermanentSendError):
        wa_meta.send_voice("+905551234567", b"x")


def test_twilio_cannot_send_voice_and_dry_run_records_it(monkeypatch):
    with pytest.raises(PermanentSendError):
        wa_twilio.send_voice("+905551234567", b"x")
    whatsapp.send_voice(PHONE, b"1234")
    assert whatsapp.OUTBOX[-1] == (PHONE, "[VOICE 4 bytes]")
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "WHATSAPP_PROVIDER", "twilio")
    with pytest.raises(PermanentSendError):
        whatsapp.send_voice(PHONE, b"x")


# ---------- news in the briefing ----------

def feed(table):
    """A fake headline fetcher: query -> list of {'title','source'}; records every call."""
    calls = []

    def fetch(query, lang="en", limit=8):
        calls.append((query, lang))
        return table.get(query, [])
    fetch.calls = calls
    return fetch


H = lambda t, s="Kaynak": {"title": t, "source": s}  # noqa: E731
TR_FEED = {
    "Türkiye gündem": [H("Genel 1"), H("Genel 2"), H("Genel 3"), H("Genel 4")],
    "ekonomi piyasalar dünya": [H("Genel 1"), H("Ekonomi 1")],
    "inşaat haberleri": [H("İnşaat 1"), H("Genel 2"), H("İnşaat 2")],
}


def test_news_has_a_general_part_and_a_part_for_the_clients_field(plus):
    db.set_client_fields(PHONE, work_area="inşaat")
    f = feed(TR_FEED)
    n = briefing.gather_news(db.get_client(PHONE), f)
    assert [h["title"] for h in n["general"]][:3] == ["Genel 1", "Genel 2", "Genel 3"]  # up to BRIEF_NEWS_TEXT, in order
    assert n["work_area"] == "inşaat" and [h["title"] for h in n["work"]] == ["İnşaat 1", "İnşaat 2"]  # 'Genel 2' not repeated
    assert ("inşaat haberleri", "tr") in f.calls


def test_english_clients_get_english_queries(plus):
    db.set_client_fields(PHONE, lang="en", work_area="banking")
    f = feed({})
    briefing.gather_news(db.get_client(PHONE), f)
    assert ("world news today", "en") in f.calls and ("banking news", "en") in f.calls


def test_without_a_field_only_general_news_and_memory_is_the_fallback(plus):
    f = feed(TR_FEED)
    n = briefing.gather_news(db.get_client(PHONE), f)
    assert n["work"] == [] and n["work_area"] == "" and not any("haberleri" in q for q, _ in f.calls)
    from app import memory
    memory.add_fact(PHONE, "work", "Yazılım şirketi kurucusu")
    f2 = feed({"Yazılım şirketi kurucusu haberleri": [H("Yazılım 1")]})
    n = briefing.gather_news(db.get_client(PHONE), f2)
    assert n["work_area"] == "Yazılım şirketi kurucusu" and n["work"][0]["title"] == "Yazılım 1"
    db.set_client_fields(PHONE, work_area="Bankacılık")  # what they told us wins over memory
    assert briefing.work_area(db.get_client(PHONE)) == "Bankacılık"


def test_headlines_are_cleaned_and_cut(plus):
    f = feed({"Türkiye gündem": [H("<b>Başlık</b>\x00 " + "x" * 400, "<i>Src</i>")]})
    [h] = briefing.gather_news(plus, f)["general"]
    assert "<" not in h["title"] and "\x00" not in h["title"] and len(h["title"]) <= 140 and h["source"] == "Src"


def test_a_failing_news_source_leaves_the_briefing_intact(plus):
    def boom(*a, **k):
        raise RuntimeError("google down")
    n = briefing.gather_news(plus, boom)
    assert n == {"general": [], "work_area": "", "work": []}


def test_news_is_cached_for_everyone_but_failures_are_not(plus):
    f = feed(TR_FEED)
    briefing.gather_news(plus, f); first = len(f.calls)
    briefing.gather_news(plus, f)
    assert len(f.calls) == first                        # second client reuses the headlines
    briefing._news_cache.clear()
    empty = feed({})
    briefing.gather_news(plus, empty); briefing.gather_news(plus, empty)
    assert len(empty.calls) == 2 * len(briefing.GENERAL_QUERIES["tr"])   # empty answers are asked again


def test_text_version_lists_both_news_parts(plus):
    db.set_client_fields(PHONE, work_area="inşaat")
    data = {**DATA, "news": briefing.gather_news(db.get_client(PHONE), feed(TR_FEED))}
    text = briefing.text_version(plus, data)
    assert "Gündem:" in text and "- Genel 1 (Kaynak)" in text and "Alanınızdan (inşaat):" in text and "- İnşaat 1 (Kaynak)" in text
    en = briefing.text_version({"lang": "en", "name": "A"}, data)
    assert "In the news:" in en and "From your field (inşaat):" in en
    assert "Gündem" not in briefing.text_version(plus, DATA)  # no news data: no news section


def test_gather_and_script_carry_the_news(plus, fake_quote, monkeypatch):
    db.set_client_fields(PHONE, work_area="inşaat")
    monkeypatch.setattr(briefing.news, "fetch", feed(TR_FEED))
    plus = db.get_client(PHONE)
    data = briefing.gather(plus, at(8, 30), quote_fn=fake_quote)
    assert data["news"]["work"][0]["title"] == "İnşaat 1"
    seen = {}
    monkeypatch.setattr(assistant, "write", lambda system, prompt: (seen.update(p=prompt, s=system), "Özet.")[1])
    briefing.script(plus, data)
    assert "İnşaat 1" in seen["p"] and "untrusted text from the web" in seen["s"]


def test_set_briefing_tool_saves_the_field_of_work(plus):
    out = json.loads(tools.run(PHONE, "set_briefing", {"work_area": "  e-ticaret  ", "time": "07:00"}))
    assert out["ok"] and out["work_area"] == "e-ticaret" and db.get_client(PHONE)["briefing_min"] == 420
    assert db.get_client(PHONE)["work_area"] == "e-ticaret"
    out = json.loads(tools.run(PHONE, "set_briefing", {"work_area": "sağlık"}))
    assert out["ok"] and db.get_client(PHONE)["briefing_min"] == 420  # time untouched when only the field is given


def test_the_delivered_briefing_includes_news(ready, monkeypatch):
    db.set_client_fields(PHONE, work_area="inşaat")
    monkeypatch.setattr(briefing.news, "fetch", feed(TR_FEED))
    text = run_briefing()[0]
    assert "Gündem:" in text and "Alanınızdan (inşaat):" in text


def test_the_same_story_from_two_searches_is_listed_once(plus):
    f = feed({"Türkiye gündem": [H("G1"), H("G2")], "ekonomi piyasalar dünya": [H("g1"), H("E1")]})
    assert [h["title"] for h in briefing.gather_news(plus, f)["general"]] == ["G1", "G2", "E1"]
