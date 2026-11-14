import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import db, markets, memory, tools

P = "+905551234567"


def call(name, **args):
    return json.loads(tools.run(P, name, args))


@pytest.fixture(autouse=True)
def quotes(monkeypatch):
    db.upsert_client(P, name="X")

    def fake(symbol):
        sym = symbol.upper()
        if sym in ("THYAO", "THYAO.IS"):
            return {"symbol": "THYAO.IS", "price": 305.5, "previous_close": 300.0, "change_pct": 1.83, "currency": "TRY"}
        if sym == "NVDA":
            return {"symbol": "NVDA", "price": 140.0, "previous_close": 139.0, "change_pct": 0.7, "currency": "USD"}
        return None

    monkeypatch.setattr(markets, "get_quote", fake)


def local_in(days, hour=9):
    d = datetime.now(ZoneInfo("Europe/Istanbul")) + timedelta(days=days)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0)


# ---------- quotes & watchlist ----------

def test_get_quote():
    assert call("get_quote", symbol="nvda")["price"] == 140.0
    assert "error" in call("get_quote", symbol="NOPE")


def test_watch_stock_with_limits_resolves_symbol():
    out = call("watch_stock", symbol="thyao", above=320, below="290")
    assert out["ok"] and out["symbol"] == "THYAO.IS"
    [w] = db.list_watch(P)
    assert (w["symbol"], w["upper"], w["lower"], w["move_pct"], w["state"]) == ("THYAO.IS", 320.0, 290.0, None, "unknown")


def test_watch_stock_without_limits_uses_default_daily_move():
    call("watch_stock", symbol="NVDA")
    assert db.list_watch(P)[0]["move_pct"] == 3.0


def test_changing_limits_rearms_the_alert():
    call("watch_stock", symbol="NVDA", above=150)
    wid = db.list_watch(P)[0]["id"]
    db.update_watch_state(wid, "above", "")
    call("watch_stock", symbol="NVDA", above=160)
    [w] = db.list_watch(P)
    assert w["upper"] == 160.0 and w["state"] == "unknown" and len(db.list_watch(P)) == 1


@pytest.mark.parametrize("args", [
    {"symbol": "NVDA", "above": 100, "below": 120},   # lower above upper
    {"symbol": "NVDA", "above": 100, "below": 100},
    {"symbol": "bad symbol!"},
    {"symbol": ""},
    {"symbol": "NOPE"},                                # unknown ticker
])
def test_watch_stock_rejects_bad_input(args):
    assert "error" in call("watch_stock", **args)
    assert db.list_watch(P) == []


def test_negative_and_garbage_limits_are_ignored_not_stored():
    call("watch_stock", symbol="NVDA", above=-5, below="abc", move_pct=float("inf"))
    w = db.list_watch(P)[0]
    assert (w["upper"], w["lower"], w["move_pct"]) == (None, None, 3.0)


def test_watchlist_size_limit(monkeypatch):
    monkeypatch.setattr(markets, "get_quote", lambda s: {
        "symbol": s.upper(), "price": 1.0, "previous_close": 1.0, "change_pct": 0, "currency": "USD"})
    for n in range(tools.MAX_WATCHES):
        assert call("watch_stock", symbol=f"S{n}")["ok"]
    assert "full" in call("watch_stock", symbol="EXTRA")["error"]
    assert call("watch_stock", symbol="S0", above=5)["ok"]  # updating an existing one still works


def test_unwatch_and_list():
    call("watch_stock", symbol="thyao", above=320)
    assert call("list_watchlist")["watchlist"][0]["above"] == 320.0
    assert call("unwatch_stock", symbol="THYAO")["ok"] is True
    assert call("unwatch_stock", symbol="THYAO")["ok"] is False
    assert call("list_watchlist")["watchlist"] == []


# ---------- reminders ----------

def test_set_reminder_stores_utc_and_lists_local():
    due = local_in(2)
    out = call("set_reminder", text="Ahmet'i ara", when_local=due.strftime("%Y-%m-%d %H:%M"))
    assert out["ok"] and out["due_local"] == due.strftime("%Y-%m-%d %H:%M")
    [r] = db.list_pending_reminders(P)
    assert r["due_at"] == (due - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:00")  # Istanbul is UTC+3 all year
    assert call("list_reminders")["reminders"][0]["due_local"] == due.strftime("%Y-%m-%d %H:%M")


def test_set_reminder_accepts_iso_t_separator():
    assert call("set_reminder", text="x y", when_local=local_in(1).strftime("%Y-%m-%dT%H:%M"))["ok"]


@pytest.mark.parametrize("when", ["yarın 9'da", "2026-13-45 99:99", "", "tomorrow"])
def test_set_reminder_rejects_unparseable_time(when):
    assert "2026-10-23 09:00" in call("set_reminder", text="hello", when_local=when)["error"]
    assert db.list_pending_reminders(P) == []


def test_set_reminder_rejects_past_far_future_and_bad_text():
    assert "past" in call("set_reminder", text="hello", when_local=local_in(-2).strftime("%Y-%m-%d %H:%M"))["error"]
    assert "year" in call("set_reminder", text="hello", when_local=local_in(500).strftime("%Y-%m-%d %H:%M"))["error"]
    assert "error" in call("set_reminder", text="", when_local=local_in(1).strftime("%Y-%m-%d %H:%M"))
    assert "error" in call("set_reminder", text="x" * 301, when_local=local_in(1).strftime("%Y-%m-%d %H:%M"))
    assert db.list_pending_reminders(P) == []


def test_reminder_limit(monkeypatch):
    monkeypatch.setattr(tools, "MAX_REMINDERS", 2)
    when = local_in(1).strftime("%Y-%m-%d %H:%M")
    assert call("set_reminder", text="one one", when_local=when)["ok"]
    assert call("set_reminder", text="two two", when_local=when)["ok"]
    assert "too many" in call("set_reminder", text="three", when_local=when)["error"]


def test_cancel_reminder_only_own():
    rid = call("set_reminder", text="mine mine", when_local=local_in(1).strftime("%Y-%m-%d %H:%M"))["id"]
    other = "+905559999999"
    assert db.cancel_reminder(other, rid) is False
    assert call("cancel_reminder", id=rid)["ok"] is True
    assert call("cancel_reminder", id=rid)["ok"] is False
    assert "error" in call("cancel_reminder", id="abc")


# ---------- shopping links ----------

def test_shopping_links_are_encoded_search_links():
    url = call("shopping_link", store="trendyol", query="kablosuz kulaklık & şarj")["url"]
    assert url.startswith("https://www.trendyol.com/sr?q=") and " " not in url and "&şarj" not in url
    assert "hepsiburada.com/ara?q=" in call("shopping_link", store="hepsiburada", query="x1")["url"]
    assert "yemeksepeti.com" in call("shopping_link", store="yemeksepeti", query="lahmacun")["url"]
    assert call("shopping_link", store="unknown-shop", query="abc")["url"].startswith("https://www.google.com/search?q=")
    assert "error" in call("shopping_link", store="trendyol", query="   ")


# ---------- memory tools ----------

def test_remember_and_forget_tools():
    assert call("remember", section="people", text="Eşi Ayşe")["status"] == "added"
    assert call("remember", section="people", text="Eşi Ayşe")["status"] == "duplicate"
    assert call("remember", section="notes", text="kart no 4111 1111 1111 1111")["status"] == "refused"
    assert call("remember", section="work", text="Works at Globex", replaces="Acme")["status"] == "added"
    assert call("forget", query="Ayşe")["removed"] == 1
    assert memory.count(P) == 1


# ---------- dispatcher safety ----------

def test_unknown_tool_and_crashing_tool_return_errors(monkeypatch):
    assert "unknown tool" in call("rm_rf", path="/")["error"]
    assert "not found" in json.loads(tools.run(P, "get_quote", "not-a-dict"))["error"]  # non-dict args tolerated
    monkeypatch.setattr(markets, "get_quote", lambda s: (_ for _ in ()).throw(RuntimeError("network down")))
    assert call("get_quote", symbol="NVDA") == {"error": "tool failed"}


def test_tool_schemas_are_well_formed():
    names = [t["name"] for t in tools.TOOLS]
    assert len(names) == len(set(names)) == len(tools._DISPATCH)
    assert set(names) == set(tools._DISPATCH)
    for t in tools.TOOLS:
        assert t["description"] and t["input_schema"]["type"] == "object"
