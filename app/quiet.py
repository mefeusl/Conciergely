"""Quiet hours: the times of day when the assistant must not send unprompted messages.

Stored per client as minutes after local midnight (quiet_start, quiet_end; -1 = off). The window may
wrap past midnight (23:00 to 08:00). Reminders the client scheduled for an exact time are NOT held back;
market alerts and follow-up checks are, and go out when the quiet window ends.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, textutil

_TIME = re.compile(r"(?<!\d)(\d{1,2})(?:[:.h](\d{2}))?\s*(am|pm)?(?!\d)")
_NONE_WORDS = {"yok", "hayir", "none", "no", "nope", "yoktur", "gerek", "istemiyorum"}


def says_none(text: str) -> bool:
    """'yok', 'none', 'no need' ... and no clock time in it."""
    f = textutil.fold(text)
    return not _TIME.search(f) and bool(_NONE_WORDS & set(re.findall(r"[a-z]+", f)))


def parse(text: str) -> tuple[int, int] | None:
    """Pull a start and end time out of free text like '23:00-08:00', '11pm to 7am', 'gece 11 ile sabah 8'."""
    f = textutil.fold(text)
    found = list(_TIME.finditer(f))
    if len(found) < 2:
        return None
    values: list[int] = []
    for idx, m in enumerate(found[:2]):
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if hour > 24 or minute > 59 or (hour == 24 and minute):
            return None
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        elif idx == 0 and not ampm and 5 <= hour <= 11 and re.search(r"gece|aksam|night|evening", f[:m.start()]):
            hour += 12  # 'gece 11' means 23:00
        values.append((hour % 24) * 60 + minute)
    start, end = values
    return None if start == end else (start, end)


def parse_hhmm(value: str) -> int | None:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _enabled(client: dict) -> bool:
    qs, qe = client.get("quiet_start", -1), client.get("quiet_end", -1)
    return qs is not None and qe is not None and qs >= 0 and qe >= 0 and qs != qe


def in_quiet(client: dict, now: datetime | None = None) -> bool:
    if not _enabled(client):
        return False
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(config.TIMEZONE))
    minute = local.hour * 60 + local.minute
    qs, qe = client["quiet_start"], client["quiet_end"]
    return qs <= minute < qe if qs < qe else (minute >= qs or minute < qe)


def next_end(client: dict, now: datetime | None = None) -> datetime:
    """The next moment (UTC) when quiet hours end."""
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(config.TIMEZONE)
    local = now.astimezone(tz)
    qe = client["quiet_end"]
    candidate = local.replace(hour=qe // 60, minute=qe % 60, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def parse_one(text: str) -> int | None:
    """A single clock time in free text ('08:30', '7am', 'sabah 8', '8.30') as minutes after midnight."""
    f = textutil.fold(text)
    m = _TIME.search(f)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if hour > 23 or minute > 59:
        return None
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return hour * 60 + minute
