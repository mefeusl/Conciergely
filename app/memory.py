"""Per-client memory map: one JSON file per client, built up as they talk.

data/memory/<digits>.json looks like:
  {"version": 1, "updated_at": "...", "sections": {"about": [{"text": "...", "added": "2026-10-22"}], ...}}

The map is added to Claude's instructions on every reply. It is kept small on purpose:
exact and near-duplicates are merged, each section is capped, and the oldest facts drop first.
Secrets (card numbers, IBANs, passwords) are refused.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config, textutil

SECTIONS = ["about", "work", "people", "routines", "preferences", "interests", "goals", "stocks", "notes"]
MAX_PER_SECTION = 25
MAX_FACT_LEN = 300

_lock = threading.RLock()

_SENSITIVE = [
    re.compile(r"(?:\d[ -]?){13,19}"),  # card-like number
    re.compile(r"\bTR\d{2}(?:\s?\d){22}\b", re.IGNORECASE),  # Turkish IBAN
    re.compile(r"\b(password|passcode|parola|sifre|şifre|cvv|cvc|otp|pin kodu|api[_ -]?key|token)\b",
               re.IGNORECASE),
]


def is_sensitive(text: str) -> bool:
    return any(p.search(text or "") for p in _SENSITIVE)


def _norm(text: str) -> str:
    return textutil.key(text)


def path_for(phone: str) -> Path:
    """File path for a client. Only digits from the phone number are used (no path tricks)."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        raise ValueError("empty phone")
    return Path(config.MEMORY_DIR) / f"{digits}.json"


def _empty() -> dict:
    return {"version": 1, "updated_at": "", "sections": {s: [] for s in SECTIONS}}


def load(phone: str) -> dict:
    with _lock:
        p = path_for(phone)
        if not p.exists():
            return _empty()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _empty()
    sections = data.get("sections") if isinstance(data, dict) else None
    out = _empty()
    if isinstance(sections, dict):
        for name in SECTIONS:
            items = sections.get(name, [])
            if isinstance(items, list):
                out["sections"][name] = [
                    {"text": str(i.get("text", ""))[:MAX_FACT_LEN], "added": str(i.get("added", ""))}
                    for i in items if isinstance(i, dict) and i.get("text")
                ]
    out["updated_at"] = str(data.get("updated_at", "")) if isinstance(data, dict) else ""
    return out


def _save(phone: str, data: dict) -> None:
    p = path_for(phone)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)  # atomic: a crash never leaves a half-written file
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_fact(phone: str, section: str, text: str, replaces: str | None = None) -> dict:
    """Remember one fact. Returns {"status": "added" | "duplicate" | "refused" | "invalid", ...}."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if section not in SECTIONS:
        return {"status": "invalid", "reason": f"section must be one of {SECTIONS}"}
    if len(text) < 3:
        return {"status": "invalid", "reason": "text too short"}
    if len(text) > MAX_FACT_LEN:
        text = text[:MAX_FACT_LEN].rstrip()
    if is_sensitive(text):
        return {"status": "refused", "reason": "looks like a secret (card, IBAN, password); not stored"}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _lock:
        data = load(phone)
        items = data["sections"][section]
        if replaces and len(replaces.strip()) >= 3:
            needle = _norm(replaces)
            items = [i for i in items if needle not in _norm(i["text"])]
        new = _norm(text)
        kept: list[dict] = []
        for item in items:
            old = _norm(item["text"])
            if old == new or new in old:
                return {"status": "duplicate"}  # already known (or already more specific)
            if old and old in new:
                continue  # the new fact is more specific: replace the old one
            kept.append(item)
        kept.append({"text": text, "added": today})
        data["sections"][section] = kept[-MAX_PER_SECTION:]
        _save(phone, data)
    return {"status": "added"}


def forget(phone: str, query: str) -> int:
    """Remove every fact containing the query text (case-insensitive). Returns how many."""
    needle = _norm(query or "")
    if len(needle) < 3:
        return 0
    removed = 0
    with _lock:
        data = load(phone)
        for name in SECTIONS:
            keep = [i for i in data["sections"][name] if needle not in _norm(i["text"])]
            removed += len(data["sections"][name]) - len(keep)
            data["sections"][name] = keep
        if removed:
            _save(phone, data)
    return removed


def forget_all(phone: str) -> bool:
    with _lock:
        p = path_for(phone)
        if p.exists():
            p.unlink()
            return True
    return False


def render(phone: str, max_chars: int = 3500) -> str:
    """The memory as plain text for Claude's instructions (newest facts win when space runs out)."""
    data = load(phone)
    lines: list[str] = []
    for name in SECTIONS:
        for item in data["sections"][name]:
            lines.append(f"[{name}] {item['text']}")
    if not lines:
        return "(nothing saved yet)"
    out = "\n".join(lines)
    while len(out) > max_chars and lines:
        lines.pop(0)
        out = "\n".join(lines)
    return out


def count(phone: str) -> int:
    return sum(len(v) for v in load(phone)["sections"].values())


def flat(phone: str) -> list[tuple[str, str]]:
    """Every fact as (section, text), in a stable order (the numbers shown by 'my data')."""
    data = load(phone)
    return [(name, item["text"]) for name in SECTIONS for item in data["sections"][name]]


def remove_nth(phone: str, number: int) -> bool:
    """Delete the fact shown as number `number` (1-based) in flat()."""
    with _lock:
        data = load(phone)
        seen = 0
        for name in SECTIONS:
            for i in range(len(data["sections"][name])):
                seen += 1
                if seen == number:
                    del data["sections"][name][i]
                    _save(phone, data)
                    return True
    return False


def edit_nth(phone: str, number: int, text: str) -> str:
    """Replace the text of fact `number`. Returns 'ok', 'refused' (looks like a secret), 'invalid' or 'missing'."""
    text = re.sub(r"\s+", " ", text or "").strip()[:MAX_FACT_LEN]
    if len(text) < 3:
        return "invalid"
    if is_sensitive(text):
        return "refused"
    with _lock:
        data = load(phone)
        seen = 0
        for name in SECTIONS:
            for item in data["sections"][name]:
                seen += 1
                if seen == number:
                    item["text"] = text
                    _save(phone, data)
                    return "ok"
    return "missing"
