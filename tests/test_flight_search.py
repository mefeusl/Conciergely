import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import config, flights, tools
from tests.conftest import PHONE

SAMPLE = {
    "best_flights": [{"flights": [{"departure_airport": {"id": "IST", "time": "2026-11-20 08:10"},
                                   "arrival_airport": {"id": "ADB", "time": "2026-11-20 09:25"},
                                   "airline": "Turkish Airlines", "flight_number": "TK 2310"}],
                      "total_duration": 75, "price": 1890}],
    "other_flights": [
        {"flights": [{"departure_airport": {"id": "IST", "time": "2026-11-20 06:00"},
                      "arrival_airport": {"id": "ESB", "time": "2026-11-20 07:05"}, "airline": "Pegasus",
                      "flight_number": "PC 100", "overnight": False},
                     {"departure_airport": {"id": "ESB", "time": "2026-11-20 09:00"},
                      "arrival_airport": {"id": "ADB", "time": "2026-11-20 10:10"}, "airline": "Pegasus",
                      "flight_number": "PC 200"}],
         "layovers": [{"id": "ESB", "duration": 115}], "total_duration": 250, "price": 990},
        {"flights": [], "price": 1}, {"price": 5}, "junk"],
    "price_insights": {"price_level": "typical", "typical_price_range": [1500, 2200], "lowest_price": 990},
}


def tomorrow(days=10):
    return (datetime.now(ZoneInfo(config.TIMEZONE)).date() + timedelta(days=days)).isoformat()


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        if self._d is ValueError:
            raise ValueError
        return self._d


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr(config, "SERPAPI_KEY", "serp-test")
    flights._cache.clear(); flights._used.clear()


def test_parse_offers_picks_best_first_and_skips_junk():
    out = flights.parse_offers(SAMPLE)
    assert [o["price"] for o in out["offers"]] == [1890, 990]  # recommended first, junk dropped
    assert out["offers"][0]["recommended"] and out["offers"][1]["stops"] == 1
    assert out["offers"][1]["layovers"] == ["ESB 115 min"] and out["price_level"] == "typical"
    assert flights.parse_offers("x")["error"] and flights.parse_offers({"error": "boom"})["error"]
    assert flights.parse_offers({"error": "Google Flights hasn't returned any results"})["offers"] == []


def test_search_validation(monkeypatch):
    called = []
    monkeypatch.setattr(flights.requests, "get", lambda *a, **k: called.append(1) or Resp(SAMPLE))
    for args in [("ist", "ist", tomorrow()), ("IS", "ADB", tomorrow()), ("IST", "ADB1", tomorrow()),
                 ("IST", "ADB", "20-11-2026"), ("IST", "ADB", "2020-01-01"), ("IST", "ADB", tomorrow(400)),
                 ("IST", "ADB", tomorrow(10), tomorrow(5))]:
        assert "error" in flights.search(PHONE, *args), args
    assert not called
    monkeypatch.setattr(config, "SERPAPI_KEY", "")
    assert "SERPAPI_KEY" in flights.search(PHONE, "IST", "ADB", tomorrow())["error"]


def test_search_request_cache_and_daily_limit(monkeypatch):
    seen = []
    def fake(url, params=None, timeout=None):
        seen.append(params)
        return Resp(SAMPLE)
    monkeypatch.setattr(flights.requests, "get", fake)
    r = flights.search(PHONE, "ist", "adb", tomorrow(), None, 2, "try")
    assert r["offers"] and r["route"] == "IST-ADB" and r["currency"] == "TRY"
    p = seen[0]
    assert p["type"] == 2 and p["adults"] == 2 and p["departure_id"] == "IST" and "return_date" not in p
    flights.search(PHONE, "IST", "ADB", tomorrow(), None, 2, "TRY")
    assert len(seen) == 1  # cached
    flights.search(PHONE, "IST", "ADB", tomorrow(), tomorrow(15))
    assert seen[1]["type"] == 1 and seen[1]["return_date"] == tomorrow(15)
    monkeypatch.setattr(config, "MAX_FLIGHT_SEARCHES_PER_DAY", 2)
    assert "limit" in flights.search(PHONE, "IST", "AYT", tomorrow())["error"]
    assert flights.search("+905000000001", "IST", "AYT", tomorrow())["offers"]  # other clients unaffected


def test_search_failures_are_contained_and_not_cached(monkeypatch):
    def boom(*a, **k): raise flights.requests.ConnectionError("x")
    monkeypatch.setattr(flights.requests, "get", boom)
    assert "could not be reached" in flights.search(PHONE, "IST", "ADB", tomorrow())["error"]
    for status, text in ((401, "rejected"), (429, "quota")):
        monkeypatch.setattr(flights.requests, "get", lambda *a, s=status, **k: Resp({}, s))
        assert text in flights.search(PHONE, "IST", "SAW", tomorrow())["error"]
    monkeypatch.setattr(flights.requests, "get", lambda *a, **k: Resp(ValueError))
    assert "unexpected" in flights.search(PHONE, "IST", "AYT", tomorrow())["error"]
    assert not flights._cache


def test_key_is_never_in_results(monkeypatch):
    monkeypatch.setattr(flights.requests, "get", lambda *a, **k: Resp(SAMPLE))
    assert "serp-test" not in json.dumps(flights.search(PHONE, "IST", "ADB", tomorrow()))


def test_tool_registered_and_runs(client, monkeypatch):
    assert any(t["name"] == "flight_search" for t in tools.TOOLS)
    monkeypatch.setattr(flights.requests, "get", lambda *a, **k: Resp(SAMPLE))
    out = json.loads(tools.run(PHONE, "flight_search", {"origin": "IST", "destination": "ADB", "date": tomorrow()}))
    assert out["offers"][0]["price"] == 1890
    bad = json.loads(tools.run(PHONE, "flight_search", {"origin": "Istanbul", "destination": "ADB", "date": tomorrow()}))
    assert "error" in bad
    bad2 = json.loads(tools.run(PHONE, "flight_search", {}))
    assert "error" in bad2


def test_prompt_never_jumps_straight_to_the_link(client):
    from app import assistant
    system = assistant.build_system(client)
    assert "do NOT just send a link" in system and "flight_search" in system
