"""Plans, feature gates, monthly caps, the migration and the admin controls."""
import io
import json
import sqlite3

import pytest
from PIL import Image

from app import adminlogic, assistant, config, db, flights, pages, plans, tools, voice, whatsapp
from conftest import FakeAnthropic, PHONE, response, text_block
from helpers import say

TRADING = ["scan_results", "backtest_setup", "position_risk", "scenario", "price_levels", "latest_news",
           "market_pulse", "log_trade", "show_portfolio", "journal_add", "journal_close", "journal_stats",
           "set_news_alerts", "use_playbook"]


@pytest.fixture(autouse=True)
def reset_assistant():
    yield
    assistant.set_client(None)


# ---------- migration ----------

def test_clients_from_before_plans_become_plus_and_new_ones_are_basic(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""CREATE TABLE clients (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL DEFAULT '', preferences TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
        lang TEXT NOT NULL DEFAULT 'tr', onboarding_step INTEGER NOT NULL DEFAULT 0,
        onboarding_done INTEGER NOT NULL DEFAULT 0, last_inbound_at TEXT NOT NULL DEFAULT '',
        pending_action TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        INSERT INTO clients(phone, created_at) VALUES('+905550001111', 'x');""")
    old.commit(); old.close()
    monkeypatch.setattr(config, "DB_PATH", str(path))
    db.init_db()
    assert db.get_client("+905550001111")["plan"] == "plus"
    assert db.get_client("+905550001111")["briefing_min"] == -1
    db.upsert_client("+905550002222", name="New")
    assert db.get_client("+905550002222")["plan"] == "basic"
    db.set_client_fields("+905550002222", plan="plus")
    db.init_db()  # running the migration again must not touch anyone
    assert db.get_client("+905550002222")["plan"] == "plus"
    db.set_client_fields("+905550001111", plan="basic")
    db.init_db()
    assert db.get_client("+905550001111")["plan"] == "basic"


def test_fresh_database_defaults():
    c = db.upsert_client(PHONE, name="A")
    assert c["plan"] == "basic" and c["briefing_min"] == -1 and c["briefing_last"] == ""


def test_unknown_plan_value_is_treated_as_basic():
    assert plans.plan_of({"plan": "gold"}) == "basic" and plans.plan_of(None) == "basic"
    assert plans.plan_of({"plan": "plus"}) == "plus"


# ---------- gates ----------

@pytest.mark.parametrize("tool", TRADING + ["set_briefing", "send_briefing_now"])
def test_basic_client_cannot_run_plus_tools(basic_client, tool):
    out = json.loads(tools.run(PHONE, tool, {}))
    assert out["error"] == "plus_feature" and "Plus" in out["message"]


@pytest.mark.parametrize("tool", ["get_quote", "watch_stock", "list_watchlist", "set_reminder", "list_reminders",
                                  "weather", "concierge_link", "shopping_link", "remember", "forget",
                                  "set_quiet_hours", "flight_status", "flight_search"])
def test_basic_client_keeps_the_everyday_tools(basic_client, tool):
    assert json.loads(tools.run(PHONE, tool, {})).get("error") != "plus_feature"


def test_plus_client_passes_the_gate(client):
    assert json.loads(tools.run(PHONE, "market_pulse", {})).get("error") != "plus_feature"
    assert plans.gate(client, "flight_search") is None


def test_unregistered_phone_is_treated_as_basic():
    assert json.loads(tools.run("+905559999999", "scenario", {}))["error"] == "plus_feature"


def test_plus_only_setting_can_unlock_a_feature_for_everyone(basic_client, monkeypatch):
    monkeypatch.setattr(config, "PLUS_ONLY", {"flights", "briefing"})
    assert plans.gate(basic_client, "scenario") is None
    assert plans.gate(basic_client, "flight_search") is not None  # the owner can still lock flights again
    monkeypatch.setattr(config, "PLUS_ONLY", {"flights", "briefing"})
    assert plans.gate(basic_client, "flight_search") is not None
    monkeypatch.setattr(config, "PLUS_ONLY", set())
    assert plans.gate(basic_client, "flight_search") is None


def test_basic_clients_do_not_even_see_plus_tools(basic_client):
    fake = FakeAnthropic(response(text_block("Merhaba")))
    assistant.set_client(fake)
    say("selam")
    names = {t["name"] for t in fake.calls[0]["tools"]}
    assert {"get_quote", "weather", "set_reminder", "web_search", "flight_search"} <= names
    assert not names & set(TRADING + ["set_briefing", "send_briefing_now"])


def test_plus_clients_see_everything(client):
    fake = FakeAnthropic(response(text_block("Merhaba")))
    assistant.set_client(fake)
    say("selam")
    names = {t["name"] for t in fake.calls[0]["tools"]}
    assert set(TRADING + ["flight_search", "set_briefing", "send_briefing_now", "weather"]) <= names


def test_system_prompt_states_the_plan(basic_client, client):
    assert "Plan: Plus" in assistant.build_system(db.get_client(PHONE))
    db.set_client_fields(PHONE, plan="basic")
    system = assistant.build_system(db.get_client(PHONE))
    assert "Plan: Basic" in system and "trading analysis" in system and "morning briefing" in system
    assert "flight search" not in system.split("Plan: Basic")[1].split("\n")[0]


# ---------- counters and limits ----------

def test_counters_are_per_client_key_and_month(monkeypatch):
    plans.record(PHONE, "voice"); plans.record(PHONE, "voice", 2); plans.record(PHONE, "files")
    plans.record("+905550009999", "voice")
    assert plans.used(PHONE, "voice") == 3 and plans.used(PHONE, "files") == 1
    assert plans.used("+905550009999", "voice") == 1 and plans.used(PHONE, "flights") == 0
    monkeypatch.setattr(plans, "month_key", lambda now=None: "2099-01")
    assert plans.used(PHONE, "voice") == 0  # a new month starts from zero
    plans.record(PHONE, "voice")
    assert plans.used(PHONE, "voice") == 1 and plans.usage_table(PHONE) == {"voice": 1}


def test_usage_is_unlimited_by_default(client, basic_client):
    for plan in ("basic", "plus", "nonsense"):
        for key in plans.USAGE_KEYS:
            assert plans.limit(plan, key) is None
    plans.record(PHONE, "voice", 10_000)
    assert plans.within_cap(db.get_client(PHONE), "voice")


def test_limit_override_json(monkeypatch):
    monkeypatch.setattr(config, "PLAN_LIMITS_JSON", '{"basic": {"voice": 5}, "plus": {"flights": 7, "x": -1}}')
    assert plans.limit("basic", "voice") == 5 and plans.limit("plus", "flights") == 7
    assert plans.limit("plus", "x") is None  # negative numbers are ignored
    assert plans.limit("plus", "voice") is None
    for bad in ("not json", "[1,2]", '{"plus": 5}', '{"plus": {"voice": true}}', '{"plus": {"voice": "9"}}'):
        monkeypatch.setattr(config, "PLAN_LIMITS_JSON", bad)
        assert plans.limit("plus", "voice") is None


def test_within_cap_boundary(client, monkeypatch):
    monkeypatch.setattr(config, "PLAN_LIMITS_JSON", '{"plus": {"voice": 2}}')
    assert plans.within_cap(client, "voice")
    plans.record(PHONE, "voice"); assert plans.within_cap(client, "voice")
    plans.record(PHONE, "voice"); assert not plans.within_cap(client, "voice")


def test_voice_notes_stop_at_the_monthly_cap(client, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "key")
    monkeypatch.setattr(config, "PLAN_LIMITS_JSON", '{"plus": {"voice": 1}}')
    monkeypatch.setattr(whatsapp, "download_media", lambda ref: (b"audio", "audio/ogg"))
    monkeypatch.setattr(voice, "transcribe", lambda audio, mime, client=None: "merhaba")
    fake = FakeAnthropic(response(text_block("Selam.")))
    assistant.set_client(fake)
    assert say("", kind="audio", media_id="1", mime="audio/ogg") == ["Duyduğum: “merhaba”\n\nSelam."]
    [blocked] = say("", kind="audio", media_id="2", mime="audio/ogg")
    assert "sesli not" in blocked and "hakkınız doldu" in blocked
    assert len(fake.calls) == 1 and plans.used(PHONE, "voice") == 1  # no second transcription, no second charge


def test_unintelligible_voice_note_is_not_charged(client, monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "key")
    monkeypatch.setattr(whatsapp, "download_media", lambda ref: (b"audio", "audio/ogg"))
    monkeypatch.setattr(voice, "transcribe", lambda audio, mime, client=None: None)
    say("", kind="audio", media_id="1", mime="audio/ogg")
    assert plans.used(PHONE, "voice") == 0


def png():
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


def test_photos_stop_at_the_monthly_cap(basic_client, monkeypatch):
    monkeypatch.setattr(config, "PLAN_LIMITS_JSON", '{"basic": {"files": 1}}')
    monkeypatch.setattr(whatsapp, "download_media", lambda ref: (png(), "image/png"))
    fake = FakeAnthropic(response(text_block("Bir fiş.")))
    assistant.set_client(fake)
    assert say("bu ne?", kind="image", media_id="5", mime="image/png") == ["Bir fiş."]
    [blocked] = say("ya bu?", kind="image", media_id="6", mime="image/png")
    assert "dosya ve fotoğraf" in blocked and len(fake.calls) == 1 and plans.used(PHONE, "files") == 1


def test_english_client_gets_an_english_cap_message(client, monkeypatch):
    db.set_client_fields(PHONE, lang="en")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "key")
    monkeypatch.setattr(config, "PLAN_LIMITS_JSON", '{"plus": {"voice": 0}}')
    [blocked] = say("", kind="audio", media_id="2", mime="audio/ogg")
    assert "voice notes" in blocked and "renews next month" in blocked


def test_flight_search_counts_only_successful_searches_and_stops_at_cap(client, monkeypatch):
    monkeypatch.setattr(config, "PLAN_LIMITS_JSON", '{"plus": {"flights": 2}}')
    results = iter([{"error": "the flight source could not be reached"}, {"offers": []}, {"offers": []}, {"offers": []}])
    monkeypatch.setattr(flights, "search", lambda *a, **k: next(results))
    args = {"origin": "IST", "destination": "ADB", "date": "2026-12-01"}
    assert "error" in json.loads(tools.run(PHONE, "flight_search", args))
    assert plans.used(PHONE, "flights") == 0  # a failed search is free
    assert "offers" in json.loads(tools.run(PHONE, "flight_search", args))
    assert "offers" in json.loads(tools.run(PHONE, "flight_search", args))
    capped = json.loads(tools.run(PHONE, "flight_search", args))
    assert "allowance" in capped["error"] and plans.used(PHONE, "flights") == 2


# ---------- erase ----------

def test_erasing_a_client_removes_their_usage(client):
    plans.record(PHONE, "voice")
    db.erase_client(PHONE)
    assert plans.used(PHONE, "voice") == 0


# ---------- admin ----------

def test_add_client_with_a_plan_and_existing_clients_keep_theirs():
    assert adminlogic.add_client("+90 555 111 22 33", "Ali", "plus") == (True, "Client saved.")
    assert db.get_client("+905551112233")["plan"] == "plus"
    adminlogic.add_client("+90 555 111 22 33", "Ali Veli", "basic")  # re-adding must not silently downgrade
    assert db.get_client("+905551112233")["plan"] == "plus" and db.get_client("+905551112233")["name"] == "Ali Veli"
    assert adminlogic.add_client("+90 555 111 22 44", "Can")[0] and db.get_client("+905551112244")["plan"] == "basic"
    ok, msg = adminlogic.add_client("+90 555 111 22 55", "Z", "gold")
    assert not ok and "basic or plus" in msg and db.get_client("+905551112255") is None


def test_set_plan_and_briefing_time(client):
    cid = client["id"]
    assert adminlogic.set_plan(cid, "plus", "07:30") == (True, "Plan saved.")
    assert db.get_client(PHONE)["briefing_min"] == 450
    assert adminlogic.set_plan(cid, "plus", "")[0] and db.get_client(PHONE)["briefing_min"] == 450  # blank keeps it
    ok, msg = adminlogic.set_plan(cid, "plus", "7.30pm")
    assert not ok and "08:30" in msg and db.get_client(PHONE)["briefing_min"] == 450
    assert adminlogic.set_plan(cid, "basic", "09:00")[0]
    assert db.get_client(PHONE)["plan"] == "basic" and db.get_client(PHONE)["briefing_min"] == -1
    assert adminlogic.set_plan(cid, "gold")[0] is False and adminlogic.set_plan(9999, "plus")[0] is False


def test_admin_pages_show_plan_and_usage(client):
    plans.record(PHONE, "voice", 3)
    detail = adminlogic.detail(client["id"])
    assert detail["plan"] == "plus" and {"key": "voice", "used": 3, "limit": None} in detail["usage"]
    html = pages.render("detail", bot="Conciergely", csrf="t", **detail)
    assert "Plan and trial" in html and "voice" in html and 'value="plus" selected' in html
    html = pages.render("dashboard", bot="Conciergely", csrf="t", **adminlogic.dashboard())
    assert ">plus<" in html and 'name="plan"' in html


def test_dashboard_shows_the_right_setup_total_per_plan():
    db.upsert_client("+905550000001", name="B"); db.upsert_client("+905550000002", name="P")
    db.set_client_fields("+905550000001", onboarding_step=2)
    db.set_client_fields("+905550000002", plan="plus", onboarding_step=2)
    html = pages.render("dashboard", bot="Conciergely", csrf="t", **adminlogic.dashboard())
    assert "step 2/5" in html and "step 2/7" in html


def test_month_key_changes_every_month():
    from datetime import datetime
    assert plans.month_key(datetime(2026, 11, 5)) == "2026-11"
    assert plans.month_key(datetime(2026, 12, 5)) != plans.month_key(datetime(2026, 11, 5))


def test_basic_is_filtered_even_when_web_search_is_off(basic_client, monkeypatch):
    monkeypatch.setattr(config, "WEB_SEARCH", False)
    fake = FakeAnthropic(response(text_block("Merhaba")))
    assistant.set_client(fake)
    say("selam")
    names = {t["name"] for t in fake.calls[0]["tools"]}
    assert "get_quote" in names and "scenario" not in names and "flight_search" in names
