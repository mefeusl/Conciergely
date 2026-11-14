"""Weather from Open-Meteo (free, no key). Works from a shared location pin or from a place name.

Coordinates are used for the one request and never stored.
"""
from __future__ import annotations

import logging
import math
import re

import requests

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
TIMEOUT = 15
LATLNG_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

# WMO weather codes -> plain words
CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


def valid_point(lat, lng) -> tuple[float, float] | None:
    try:
        la, lo = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(la) and math.isfinite(lo)) or not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None
    return round(la, 4), round(lo, 4)


def parse_latlng(text: str) -> tuple[float, float] | None:
    m = LATLNG_RE.match(text or "")
    return valid_point(m.group(1), m.group(2)) if m else None


def _get(url: str, params: dict) -> dict:
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def geocode(place: str) -> tuple[float, float, str] | None:
    name = " ".join(str(place or "").split())[:80]
    if not name:
        return None
    data = _get(GEOCODE_URL, {"name": name, "count": 1, "language": "en", "format": "json"})
    hits = data.get("results") if isinstance(data, dict) else None
    if not hits:
        return None
    h = hits[0]
    point = valid_point(h.get("latitude"), h.get("longitude"))
    if not point:
        return None
    label = ", ".join(x for x in (h.get("name"), h.get("admin1"), h.get("country")) if x)
    return point[0], point[1], label


def _num(v, nd: int = 0):
    return round(v, nd) if isinstance(v, (int, float)) and math.isfinite(v) else None


def summarize(data: dict) -> dict:
    """Open-Meteo answer -> a small dict Claude can read out."""
    cur = (data or {}).get("current") or {}
    daily = (data or {}).get("daily") or {}
    days = []
    dates = daily.get("time") or []
    for i, day in enumerate(dates[:3]):
        def at(key):
            arr = daily.get(key) or []
            return arr[i] if i < len(arr) else None
        days.append({"date": day, "condition": CODES.get(at("weather_code"), "unknown"),
                     "min_c": _num(at("temperature_2m_min")), "max_c": _num(at("temperature_2m_max")),
                     "rain_chance_pct": _num(at("precipitation_probability_max"))})
    if not cur and not days:
        return {"error": "the weather source returned nothing"}
    return {"now": {"condition": CODES.get(cur.get("weather_code"), "unknown"),
                    "temp_c": _num(cur.get("temperature_2m"), 1), "feels_like_c": _num(cur.get("apparent_temperature"), 1),
                    "humidity_pct": _num(cur.get("relative_humidity_2m")), "wind_kmh": _num(cur.get("wind_speed_10m"))},
            "days": days, "source": "Open-Meteo"}


def lookup(lat=None, lng=None, place: str | None = None) -> dict:
    """Weather for a point or a named place. Never raises."""
    try:
        label = ""
        point = valid_point(lat, lng) if lat is not None and lng is not None else None
        if point is None:
            if not place:
                return {"error": "give a place name, or the client must share a location pin"}
            found = geocode(place)
            if not found:
                return {"error": f"could not find a place called '{str(place)[:40]}'"}
            point, label = (found[0], found[1]), found[2]
        data = _get(FORECAST_URL, {
            "latitude": point[0], "longitude": point[1], "timezone": "auto", "forecast_days": 3,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"})
        out = summarize(data)
        if label:
            out["place"] = label
        return out
    except (requests.RequestException, ValueError):
        return {"error": "the weather source could not be reached"}
