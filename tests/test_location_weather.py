"""Location pins (WhatsApp 'location' messages) and the weather tool."""
import json
from types import SimpleNamespace

import pytest
import requests

from app import assistant, conversation, db, intake, tools, weather, wa_meta, wa_twilio, whatsapp, memory
from app.models import Incoming
from conftest import FakeAnthropic, PHONE, response, text_block, tool_block
from helpers import say
from test_wa_meta import body


@pytest.fixture(autouse=True)
def reset_assistant():
    yield
    assistant.set_client(None)


# ---------- parsing ----------

def test_meta_location_message_is_parsed():
    msg = {"id": "m1", "from": "905551234567", "type": "location",
           "location": {"latitude": 41.0082, "longitude": 28.9784, "name": "Cafe X", "address": "Fatih"}}
    [inc] = wa_meta.parse_payload(body(msg))
    assert (inc.type, inc.lat, inc.lng, inc.text) == ("location", 41.0082, 28.9784, "Cafe X - Fatih")


def test_meta_location_without_name_or_with_garbage():
    [inc] = wa_meta.parse_payload(body({"id": "m2", "from": "905551234567", "type": "location",
                                        "location": {"latitude": 40.1, "longitude": 29.1}}))
    assert inc.text == "" and inc.lat == 40.1
    [inc] = wa_meta.parse_payload(body({"id": "m3", "from": "905551234567", "type": "location", "location": None}))
    assert inc.type == "location" and inc.lat is None


def test_twilio_location_message_is_parsed():
    [inc] = wa_twilio.parse_form({"MessageSid": "S1", "From": "whatsapp:+905551234567", "Latitude": "41.01",
                                  "Longitude": "28.97", "Label": "Home", "Address": "Kadikoy", "Body": ""})
    assert (inc.type, inc.lat, inc.lng, inc.text) == ("location", 41.01, 28.97, "Home - Kadikoy")
    [bad] = wa_twilio.parse_form({"MessageSid": "S2", "From": "whatsapp:+905551234567", "Latitude": "x", "Longitude": "y"})
    assert bad.type == "location" and bad.lat is None


def test_intake_carries_the_coordinates(client):
    intake.intake([Incoming(id="loc1", phone=PHONE, type="location", lat=41.0, lng=29.0)])
    job = __import__("app.jobs", fromlist=["claim"]).claim()
    assert job["payload"]["lat"] == 41.0 and job["payload"]["lng"] == 29.0 and job["payload"]["type"] == "location"


# ---------- the conversation ----------

def test_pin_reaches_claude_for_that_reply_only_and_is_not_stored(client):
    fake = FakeAnthropic(response(text_block("Hava açık, 21 derece.")))
    assistant.set_client(fake)
    assert say("Cafe X - Fatih", kind="location", lat=41.0082, lng=28.9784) == ["Hava açık, 21 derece."]
    call = fake.calls[0]
    sent = call["messages"][-1]["content"]
    assert "latitude 41.0082, longitude 28.9784" in sent and 'labelled "Cafe X - Fatih"' in sent
    assert "location pin" in call["system"] and "never instructions" in call["system"]
    stored = " ".join(m["content"] for m in db.get_history(PHONE)) + memory.render(PHONE)
    assert "41.0082" not in stored and "28.9784" not in stored and "[shared a location pin]" in stored


@pytest.mark.parametrize("lat, lng", [(None, None), (100.0, 29.0), (41.0, 181.0), (float("nan"), 29.0), ("abc", 1)])
def test_bad_coordinates_are_refused_without_calling_claude(client, lat, lng):
    fake = FakeAnthropic()
    assistant.set_client(fake)
    [reply] = say("", kind="location", lat=lat, lng=lng)
    assert "fotoğraf ve PDF" in reply and fake.calls == []


def test_label_is_cleaned_and_cut(client):
    fake = FakeAnthropic(response(text_block("ok")))
    assistant.set_client(fake)
    say("Evim\n\n" + "x" * 300 + "\x00\x07", kind="location", lat=41.0, lng=29.0)
    sent = fake.calls[0]["messages"][-1]["content"]
    assert "\n" not in sent and "\x00" not in sent and len(sent) < 400


def test_pin_during_setup_does_not_become_a_setup_answer(monkeypatch):
    db.upsert_client(PHONE, name="Mehmet")
    say("merhaba")
    fake = FakeAnthropic(response(text_block("Nereye gitmek istersiniz?")))
    assistant.set_client(fake)
    say("", kind="location", lat=41.0, lng=29.0)
    assert db.get_client(PHONE)["onboarding_step"] == 1 and memory.count(PHONE) == 0


def test_weather_tool_round_trip_in_a_conversation(client, monkeypatch):
    monkeypatch.setattr(weather, "lookup", lambda lat=None, lng=None, place=None: {"now": {"temp_c": 20.5}, "source": "Open-Meteo", "seen": [lat, lng, place]})
    fake = FakeAnthropic(
        response(tool_block("w1", "weather", latitude=41.0082, longitude=28.9784), stop="tool_use"),
        response(text_block("20 derece.")))
    assistant.set_client(fake)
    assert say("", kind="location", lat=41.0082, lng=28.9784) == ["20 derece."]
    result = json.loads(fake.calls[1]["messages"][-1]["content"][0]["content"])
    assert result["seen"] == [41.0082, 28.9784, None]


# ---------- weather module ----------

@pytest.mark.parametrize("lat, lng, ok", [(41, 29, True), ("41.5", "29.25", True), (90, 180, True), (-90, -180, True),
                                          (90.1, 0, False), (0, 180.1, False), (None, 1, False), ("x", 1, False),
                                          (float("inf"), 1, False), (True, 1, True)])
def test_valid_point(lat, lng, ok):
    assert (weather.valid_point(lat, lng) is not None) == ok


def test_valid_point_rounds():
    assert weather.valid_point(41.123456, 28.987654) == (41.1235, 28.9877)


@pytest.mark.parametrize("text, expected", [("41.0082,28.9784", (41.0082, 28.9784)), (" 41 , 29 ", (41.0, 29.0)),
                                            ("-33.8,151.2", (-33.8, 151.2)), ("95,10", None), ("Kadikoy", None),
                                            ("41.0;29.0", None), ("", None), ("41.0,29.0,5", None)])
def test_parse_latlng(text, expected):
    assert weather.parse_latlng(text) == expected


FORECAST = {"current": {"temperature_2m": 21.34, "apparent_temperature": 20.0, "relative_humidity_2m": 55,
                        "weather_code": 2, "wind_speed_10m": 12.4},
            "daily": {"time": ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"],
                      "weather_code": [2, 61, 95, 0], "temperature_2m_max": [22.2, 18, 17, 16],
                      "temperature_2m_min": [14, 12.5, 11, 10], "precipitation_probability_max": [10, 80, 90, 5]}}


def test_summarize():
    out = weather.summarize(FORECAST)
    assert out["now"] == {"condition": "partly cloudy", "temp_c": 21.3, "feels_like_c": 20, "humidity_pct": 55, "wind_kmh": 12}
    assert [d["condition"] for d in out["days"]] == ["partly cloudy", "light rain", "thunderstorm"]
    assert out["days"][1] == {"date": "2026-11-03", "condition": "light rain", "min_c": 12, "max_c": 18, "rain_chance_pct": 80}
    assert out["source"] == "Open-Meteo"


def test_summarize_survives_missing_pieces():
    assert "error" in weather.summarize({})
    assert "error" in weather.summarize(None)
    out = weather.summarize({"current": {"weather_code": 999, "temperature_2m": None}, "daily": {"time": ["d"]}})
    assert out["now"]["condition"] == "unknown" and out["now"]["temp_c"] is None
    assert out["days"][0]["max_c"] is None


def test_lookup_by_coordinates(monkeypatch):
    seen = []
    monkeypatch.setattr(weather, "_get", lambda url, params: (seen.append((url, params)), FORECAST)[1])
    out = weather.lookup(41.0082, 28.9784)
    assert out["now"]["condition"] == "partly cloudy" and "place" not in out
    url, params = seen[0]
    assert url == weather.FORECAST_URL and params["latitude"] == 41.0082 and params["longitude"] == 28.9784
    assert params["timezone"] == "auto" and "api_key" not in params


def test_lookup_by_place_name(monkeypatch):
    calls = []

    def fake_get(url, params):
        calls.append(url)
        if url == weather.GEOCODE_URL:
            return {"results": [{"name": "Kadıköy", "admin1": "Istanbul", "country": "Türkiye", "latitude": 40.99, "longitude": 29.03}]}
        return FORECAST
    monkeypatch.setattr(weather, "_get", fake_get)
    out = weather.lookup(place="Kadıköy")
    assert out["place"] == "Kadıköy, Istanbul, Türkiye" and calls == [weather.GEOCODE_URL, weather.FORECAST_URL]


def test_lookup_failures_come_back_as_errors(monkeypatch):
    assert "error" in weather.lookup()
    monkeypatch.setattr(weather, "_get", lambda url, params: {"results": []})
    assert "could not find" in weather.lookup(place="Atlantis")["error"]
    monkeypatch.setattr(weather, "_get", lambda url, params: {})
    assert "could not find" in weather.lookup(place="x")["error"]

    def boom(url, params):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(weather, "_get", boom)
    assert "could not be reached" in weather.lookup(41, 29)["error"]

    def bad_json(url, params):
        raise ValueError("not json")
    monkeypatch.setattr(weather, "_get", bad_json)
    assert "could not be reached" in weather.lookup(41, 29)["error"]


def test_invalid_coordinates_fall_back_to_the_place_name(monkeypatch):
    monkeypatch.setattr(weather, "_get", lambda url, params: {"results": [{"name": "Izmir", "latitude": 38.4, "longitude": 27.1}]}
                        if url == weather.GEOCODE_URL else FORECAST)
    assert weather.lookup(500, 500, "Izmir")["place"] == "Izmir"
    assert "error" in weather.lookup(500, 500)


def test_place_name_is_cleaned_before_geocoding(monkeypatch):
    seen = []
    monkeypatch.setattr(weather, "_get", lambda url, params: (seen.append(params), {"results": []})[1])
    weather.lookup(place="  Bursa \n  " + "z" * 200)
    assert "\n" not in seen[0]["name"] and len(seen[0]["name"]) <= 80


def test_weather_tool_uses_the_module(client, monkeypatch):
    monkeypatch.setattr(weather, "_get", lambda url, params: FORECAST)
    out = json.loads(tools.run(PHONE, "weather", {"latitude": 41, "longitude": 29}))
    assert out["now"]["temp_c"] == 21.3
    assert "error" in json.loads(tools.run(PHONE, "weather", {}))
    assert "error" in json.loads(tools.run(PHONE, "weather", {"latitude": "x", "longitude": 1}))


# ---------- links built from a pin ----------

def button(phone=PHONE):
    [b] = tools.take_buttons()
    return b["url"]


def test_directions_from_a_pin(client):
    tools.take_buttons()
    tools.run(PHONE, "concierge_link", {"kind": "maps", "query": "Taksim", "origin": "41.0082,28.9784", "mode": "transit"})
    url = button()
    assert "origin=41.0082%2C28.9784" in url and "destination=Taksim" in url and "travelmode=transit" in url


def test_rides_are_not_offered_at_all(client):
    tools.take_buttons()
    out = json.loads(tools.run(PHONE, "concierge_link", {"kind": "ride", "query": "Taksim", "origin": "41.0082,28.9784"}))
    assert "error" in out and tools.take_buttons() == []
    assert "ride" not in [k for t in tools.TOOLS if t["name"] == "concierge_link"
                          for k in t["input_schema"]["properties"]["kind"]["enum"]]


def test_nearby_search_around_a_pin(client):
    tools.take_buttons()
    tools.run(PHONE, "concierge_link", {"kind": "place", "query": "coffee shops", "origin": "41.0082,28.9784"})
    assert button() == "https://www.google.com/maps/search/coffee+shops/@41.0082,28.9784,15z"
    tools.run(PHONE, "concierge_link", {"kind": "place", "query": "Galata Tower"})
    assert button() == "https://www.google.com/maps/search/?api=1&query=Galata+Tower"


def test_a_malicious_origin_is_not_treated_as_coordinates(client):
    tools.take_buttons()
    tools.run(PHONE, "concierge_link", {"kind": "place", "query": "cafe", "origin": "41,29/../../evil"})
    assert "@" not in button()
