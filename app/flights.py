"""Live flight status through AeroDataBox (RapidAPI). Set AERODATABOX_KEY to turn it on.

Without a key the tool says so and Claude falls back to web search. The response parser is written
against AeroDataBox's documented shape and reads every field defensively.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from . import config

log = logging.getLogger(__name__)

FLIGHT_RE = re.compile(r"^[A-Z0-9]{2}\d{1,4}[A-Z]?$")
HOST = "aerodatabox.p.rapidapi.com"


def normalize_flight(raw: str) -> str:
    return re.sub(r"[\s\-]", "", str(raw or "")).upper()


def _leg(part: dict | None) -> dict:
    part = part or {}
    airport = part.get("airport") or {}
    sched, revised = part.get("scheduledTime") or {}, part.get("revisedTime") or {}
    return {
        "airport": airport.get("iata") or airport.get("name") or "",
        "airport_name": airport.get("name") or "",
        "scheduled_local": sched.get("local") or "",
        "estimated_local": revised.get("local") or "",
        "terminal": part.get("terminal") or "",
        "gate": part.get("gate") or "",
    }


def parse(data) -> list[dict]:
    items = data.get("items", []) if isinstance(data, dict) else (data or [])
    out = []
    for f in items[:5]:
        if not isinstance(f, dict):
            continue
        out.append({
            "flight": f.get("number") or "",
            "airline": (f.get("airline") or {}).get("name") or "",
            "status": f.get("status") or "",
            "departure": _leg(f.get("departure")),
            "arrival": _leg(f.get("arrival")),
        })
    return out


def lookup(flight: str, date: str | None = None) -> dict:
    """Returns {'flights': [...]} or {'error': '...'}. Never raises."""
    number = normalize_flight(flight)
    if not FLIGHT_RE.match(number):
        return {"error": "flight number looks wrong; use a code like TK1971"}
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    day = today
    if date:
        try:
            day = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return {"error": "date must look like 2026-10-24"}
        if not today - timedelta(days=2) <= day <= today + timedelta(days=330):
            return {"error": "date is outside the range live data covers"}
    if not config.AERODATABOX_KEY:
        return {"error": "live flight data is not configured; use web_search instead"}
    try:
        resp = requests.get(
            f"https://{HOST}/flights/number/{number}/{day.isoformat()}",
            headers={"X-RapidAPI-Key": config.AERODATABOX_KEY, "X-RapidAPI-Host": HOST},
            params={"withAircraftImage": "false", "withLocation": "false"}, timeout=15)
    except requests.RequestException as exc:
        log.warning("flight lookup failed: %s", type(exc).__name__)
        return {"error": "flight service unreachable"}
    if resp.status_code == 404:
        return {"error": "no flight found for that number and date"}
    if resp.status_code >= 400:
        log.warning("flight lookup HTTP %s", resp.status_code)
        return {"error": f"flight service error {resp.status_code}"}
    try:
        flights = parse(resp.json())
    except ValueError:
        return {"error": "flight service sent an unreadable answer"}
    return {"flights": flights} if flights else {"error": "no flight found for that number and date"}


# ---------- fares and times for a route (Google Flights data via SerpApi) ----------

IATA_RE = re.compile(r"^[A-Z]{3}$")
SERP_URL = "https://serpapi.com/search.json"
CACHE_SECONDS = 15 * 60
MAX_OFFERS = 5
_cache: dict[tuple, tuple[float, dict]] = {}
_used: dict[tuple[str, str], int] = {}  # (phone, day) -> searches; resets on restart, which only errs generous


def _offer(it: dict) -> dict | None:
    segs = [s for s in (it.get("flights") or []) if isinstance(s, dict)]
    if not segs or not isinstance(it.get("price"), (int, float)):
        return None
    first, last = segs[0], segs[-1]
    return {
        "price": it["price"],
        "airlines": sorted({str(s.get("airline") or "") for s in segs if s.get("airline")}),
        "flight_numbers": [str(s.get("flight_number") or "") for s in segs],
        "departs": (first.get("departure_airport") or {}).get("time") or "",
        "from": (first.get("departure_airport") or {}).get("id") or "",
        "arrives": (last.get("arrival_airport") or {}).get("time") or "",
        "to": (last.get("arrival_airport") or {}).get("id") or "",
        "total_minutes": it.get("total_duration"),
        "stops": len(segs) - 1,
        "layovers": [f"{l.get('id', '')} {l.get('duration', '')} min".strip() for l in (it.get("layovers") or [])
                     if isinstance(l, dict)],
        "overnight": any(bool(s.get("overnight")) for s in segs),
    }


def parse_offers(data) -> dict:
    if not isinstance(data, dict):
        return {"error": "unexpected answer from the flight source"}
    if data.get("error"):
        text = str(data["error"])
        if "hasn't returned any results" in text or "no results" in text.lower():
            return {"offers": [], "note": "no flights found for that route and date"}
        return {"error": "the flight source reported a problem"}
    offers = []
    for group in ("best_flights", "other_flights"):
        for it in data.get(group) or []:
            if isinstance(it, dict):
                o = _offer(it)
                if o:
                    o["recommended"] = group == "best_flights"
                    offers.append(o)
    offers.sort(key=lambda o: (not o["recommended"], o["price"]))
    out = {"offers": offers[:MAX_OFFERS]}
    insights = data.get("price_insights")
    if isinstance(insights, dict):
        out["price_level"] = insights.get("price_level")
        out["typical_price_range"] = insights.get("typical_price_range")
        out["lowest_price"] = insights.get("lowest_price")
    return out


def search(phone: str, origin: str, destination: str, date: str, return_date: str | None = None,
           adults: int = 1, currency: str = "TRY") -> dict:
    """Priced flight options between two airports. Never raises; errors come back as {'error': ...}."""
    if not config.SERPAPI_KEY:
        return {"error": "fare search is not configured (no SERPAPI_KEY); offer the Google Flights button instead"}
    o, d = str(origin or "").strip().upper(), str(destination or "").strip().upper()
    if not (IATA_RE.match(o) and IATA_RE.match(d)) or o == d:
        return {"error": "origin and destination must be different 3-letter airport codes like IST, SAW, ADB, AYT"}
    tz = ZoneInfo(config.TIMEZONE)
    today = datetime.now(tz).date()
    try:
        day = datetime.strptime(str(date), "%Y-%m-%d").date()
        back = datetime.strptime(str(return_date), "%Y-%m-%d").date() if return_date else None
    except ValueError:
        return {"error": "dates must look like 2026-11-20"}
    if not today <= day <= today + timedelta(days=330) or (back and not day <= back <= day + timedelta(days=330)):
        return {"error": "dates must be from today up to 330 days ahead, and the return after the departure"}
    try:
        adults = int(adults)
    except (TypeError, ValueError):
        adults = 1
    adults = max(1, min(adults, 9))
    currency = re.sub(r"[^A-Z]", "", str(currency or "TRY").upper())[:3] or "TRY"
    key = (o, d, str(day), str(back), adults, currency)
    import time as _t
    hit = _cache.get(key)
    if hit and _t.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    used_key = (phone, str(today))
    if _used.get(used_key, 0) >= config.MAX_FLIGHT_SEARCHES_PER_DAY:
        return {"error": "daily flight search limit reached; offer the Google Flights button instead"}
    params = {"engine": "google_flights", "departure_id": o, "arrival_id": d, "outbound_date": str(day),
              "type": 1 if back else 2, "adults": adults, "currency": currency, "hl": "en",
              "api_key": config.SERPAPI_KEY}
    if back:
        params["return_date"] = str(back)
    try:
        resp = requests.get(SERP_URL, params=params, timeout=40)
    except requests.RequestException:
        return {"error": "the flight source could not be reached"}
    _used[used_key] = _used.get(used_key, 0) + 1
    if resp.status_code in (401, 403):
        log.error("SerpApi refused the key (HTTP %s)", resp.status_code)
        return {"error": "the flight source rejected the access key"}
    if resp.status_code == 429:
        return {"error": "the flight search quota is used up"}
    try:
        data = resp.json()
    except ValueError:
        return {"error": "unexpected answer from the flight source"}
    result = parse_offers(data)
    result.update({"route": f"{o}-{d}", "date": str(day), "return_date": str(back) if back else None,
                   "currency": currency, "source": "Google Flights via SerpApi",
                   "note": "fares change quickly; the client books on the airline or travel site"})
    if "error" not in result:
        _cache[key] = (_t.time(), result)
        if len(_cache) > 200:
            _cache.pop(next(iter(_cache)))
    return result
