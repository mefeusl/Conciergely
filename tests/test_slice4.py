"""Slice 4: repeating reminders (bills), currency/gold aliases, parcels, prayer times, KAP query."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import adminlogic, assistant, db, info_tools, markets, news, scheduler, tools

PHONE = "+905551234567"


def _soon(day=15):
    """A date about 40 days ahead on the given day of the month, as the tool wants it."""
    from datetime import timedelta
    t = (datetime.now(TZ) + timedelta(days=40)).replace(day=day)
    return t.strftime("%Y-%m-%d 09:00")
TZ = ZoneInfo("Europe/Istanbul")


def _client():
    adminlogic.add_client(PHONE, "Ali", "basic")
    db.set_client_fields(PHONE, onboarding_done=1)


def test_next_due_sticks_to_the_day_of_month():
    jan31 = datetime(2027, 1, 31, 9, 0, tzinfo=TZ).astimezone(timezone.utc)
    feb = scheduler.next_due(jan31, "monthly:31")
    assert feb.astimezone(TZ).strftime("%Y-%m-%d %H:%M") == "2027-02-28 09:00"
    mar = scheduler.next_due(feb, "monthly:31")
    assert mar.astimezone(TZ).strftime("%Y-%m-%d") == "2027-03-31"  # no drift to the 28th
    assert scheduler.next_due(jan31, "yearly:31").astimezone(TZ).strftime("%Y-%m-%d") == "2028-01-31"
    assert scheduler.next_due(jan31, "") is None and scheduler.next_due(jan31, "weekly:1") is None


def test_set_reminder_repeat_creates_the_next_one_when_it_fires():
    _client()
    out = json.loads(tools.set_reminder(PHONE, {"text": "Elektrik faturası", "when_local": _soon(15),
                                               "repeat": "monthly", "remind_before_minutes": 1440}))
    assert out["ok"] and out["repeats"] == "monthly"
    main = [r for r in db.list_pending_reminders(PHONE) if r["kind"] == "main"][0]
    assert main["repeat"] == "monthly:15"
    # make it due now and tick
    with db.conn() as c:
        c.execute("UPDATE reminders SET due_at=? WHERE id=?", (db.to_iso(db.now_utc()), main["id"]))
    scheduler.tick_reminders()
    pending = db.list_pending_reminders(PHONE)
    mains = [r for r in pending if r["kind"] == "main"]
    assert len(mains) == 1 and mains[0]["text"] == "Elektrik faturası" and mains[0]["repeat"] == "monthly:15"
    assert [r for r in pending if r["kind"] == "before"]  # the day-before heads-up came along


def test_a_cancelled_repeat_stops():
    _client()
    rid = json.loads(tools.set_reminder(PHONE, {"text": "Kira", "when_local": _soon(1), "repeat": "monthly"}))["id"]
    assert db.cancel_reminder(PHONE, rid)
    with db.conn() as c:
        c.execute("UPDATE reminders SET due_at=?", (db.to_iso(db.now_utc()),))
    assert scheduler.tick_reminders() == 0
    assert db.list_pending_reminders(PHONE) == []


def test_bad_repeat_value_is_refused():
    _client()
    assert "error" in json.loads(tools.set_reminder(PHONE, {"text": "x y", "when_local": _soon(1), "repeat": "daily"}))


def test_currency_and_gold_aliases():
    assert markets.normalize_symbol("dolar") == "USDTRY=X" and markets.normalize_symbol("Euro") == "EURTRY=X"
    assert markets.normalize_symbol("ons altın") == "GC=F" and markets.normalize_symbol("thyao") == "THYAO"
    assert markets.market_is_open("USDTRY=X")


def test_watching_the_dollar_works_end_to_end(monkeypatch):
    _client()
    monkeypatch.setattr(markets, "get_quote", lambda s: {"symbol": s, "price": 34.1, "currency": "TRY", "previous_close": 34.0, "change_pct": 0.3})
    out = json.loads(tools.watch_stock(PHONE, {"symbol": "dolar", "above": 35}))
    assert out["ok"] and out["symbol"] == "USDTRY=X"


def test_parcel_links():
    out = json.loads(info_tools.track_parcel(PHONE, {"carrier": "UPS", "number": "1Z 999 AA1 0123456784"}))
    assert out["link"].endswith("1Z999AA10123456784") and "live status" in out["note"]
    aras = json.loads(info_tools.track_parcel(PHONE, {"carrier": "Aras Kargo", "number": "123456789012"}))
    assert aras["carrier"] == "Aras Kargo" and "enter the number" in aras["note"]
    assert "error" in json.loads(info_tools.track_parcel(PHONE, {"carrier": "bilinmez", "number": "123456789012"}))
    assert "error" in json.loads(info_tools.track_parcel(PHONE, {"carrier": "ups", "number": "12"}))


def test_prayer_times_parse_and_failure(monkeypatch):
    class R:
        status_code = 200
        def json(self):
            return {"data": {"timings": {"Fajr": "05:41 (+03)", "Sunrise": "07:05 (+03)", "Dhuhr": "12:20 (+03)",
                                         "Asr": "15:10 (+03)", "Maghrib": "17:35 (+03)", "Isha": "18:55 (+03)"}}}
    seen = {}
    monkeypatch.setattr(info_tools.requests, "get", lambda url, **kw: (seen.update(kw), R())[1])
    out = json.loads(info_tools.prayer_times(PHONE, {"city": "Ankara"}))
    assert out["times"]["İmsak"] == "05:41" and out["times"]["Yatsı"] == "18:55" and seen["params"]["method"] == 13
    monkeypatch.setattr(info_tools.requests, "get", lambda url, **kw: (_ for _ in ()).throw(info_tools.requests.ConnectionError()))
    assert json.loads(info_tools.prayer_times(PHONE, {}))["unavailable"]


def test_kap_is_in_the_bist_news_query_and_rules_are_in_the_prompt():
    assert news.query_for("THYAO.IS") == "THYAO hisse OR KAP" and news.query_for("NVDA") == "NVDA stock"
    _client()
    system = assistant.build_system(db.get_client(PHONE))
    assert "repeat='monthly'" in system and "track_parcel" in system and "altın" in system and "randevu" in system
    names = {t["name"] for t in tools.TOOLS}
    assert {"track_parcel", "prayer_times"} <= names
