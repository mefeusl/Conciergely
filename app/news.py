"""Headlines from Google News RSS, plus opt-in material-news alerts for a client's own symbols.

Headlines are UNTRUSTED text from the internet: they are length-limited and stripped of control characters, and
anything handed to Claude is labelled as data. The RSS parser refuses documents that declare entities (so a
hostile feed cannot expand into a memory bomb) and the download is size-capped.
"""
from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

from . import config, db, jobs

log = logging.getLogger(__name__)

MAX_BYTES = 1_000_000
MAX_ALERTS_PER_DAY = 3
SYMBOL_OK = re.compile(r"^[A-Z0-9.\-=^]{1,15}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")

MATERIAL = re.compile(
    r"\b(kap|bilan[cç]o|temett[uü]|bedelsiz|bedelli|sermaye art|geri al[ıi]m|satın alma|birleşme|ihale|dava|soruşturma|"
    r"iflas|ceza|faiz karar|sipariş|ortaklık|"
    r"earnings|guidance|dividend|buyback|acquisition|acquire|merger|lawsuit|sues|investigation|probe|recall|"
    r"bankruptcy|downgrade|upgrade|halt|delist|fda|sec charges|layoffs|ceo (?:steps|resign)|profit warning|"
    r"stock split|offering)\b", re.IGNORECASE)


def clean(text: str, limit: int = 200) -> str:
    return _CTRL.sub(" ", re.sub(r"<[^>]+>", "", text or "")).strip()[:limit]


def parse_rss(raw: bytes, limit: int = 10) -> list[dict]:
    if len(raw) > MAX_BYTES or b"<!ENTITY" in raw or b"<!DOCTYPE" in raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    items = []
    for it in root.iter("item"):
        title = clean(it.findtext("title") or "")
        if not title:
            continue
        source = clean(it.findtext("source") or "", 60)
        when = ""
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "").strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            pass
        items.append({"title": title, "source": source, "published": when})
        if len(items) >= limit:
            break
    return items


def _get(url: str) -> bytes | None:
    try:
        with requests.get(url, timeout=10, stream=True, headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status_code != 200:
                return None
            data = b""
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > MAX_BYTES:
                    return None
            return data
    except requests.RequestException:
        return None


def query_for(symbol: str) -> str:
    sym = symbol.upper()
    return f"{sym[:-3]} hisse OR KAP" if sym.endswith(".IS") else f"{sym} stock"  # KAP disclosures show up in the news feed


def fetch(query: str, lang: str = "en", limit: int = 8, days: int = 2) -> list[dict]:
    query = clean(query, 80)
    if not query:
        return []
    tr = lang == "tr"
    url = ("https://news.google.com/rss/search?q=" + quote(query + f" when:{max(1, min(int(days), 7))}d") +
           ("&hl=tr&gl=TR&ceid=TR:tr" if tr else "&hl=en-US&gl=US&ceid=US:en"))
    raw = _get(url)
    return parse_rss(raw, limit) if raw else []


def is_material(title: str) -> bool:
    return bool(MATERIAL.search(title or ""))


def _key(symbol: str, title: str) -> str:
    return hashlib.sha1(f"{symbol}|{title.lower()}".encode("utf-8")).hexdigest()[:20]


def _symbols_for(phone: str) -> list[str]:
    syms = [w["symbol"] for w in db.list_watch(phone)]
    from . import portfolio
    syms += [s for s, p in portfolio.positions(phone).items() if p["qty"] > 0]
    out: list[str] = []
    for s in syms:
        if SYMBOL_OK.match(s) and s not in out and "=" not in s and "^" not in s:
            out.append(s)
    return out[:10]


def run_cycle(fetcher=None) -> int:
    """Queue at most MAX_ALERTS_PER_DAY material headlines per opted-in client. Returns messages queued."""
    fetcher = fetcher or fetch
    queued = 0
    today = db.now_utc().strftime("%Y-%m-%d")
    with db.conn() as c:
        clients = [dict(r) for r in c.execute("SELECT phone, lang FROM clients WHERE active=1 AND news_alerts=1")]
    cache: dict[str, list] = {}
    for cl in clients:
        phone, lang = cl["phone"], cl["lang"]
        with db.conn() as c:
            known = c.execute("SELECT COUNT(*) FROM news_seen WHERE phone=?", (phone,)).fetchone()[0]
            sent_today = c.execute("SELECT COUNT(*) FROM news_seen WHERE phone=? AND sent=1 AND seen_at LIKE ?",
                                   (phone, today + "%")).fetchone()[0]
        first_run = known == 0
        if first_run:  # remember that the baseline was taken, even if nothing was found
            with db.conn() as c:
                c.execute("INSERT OR IGNORE INTO news_seen(phone,key,seen_at,sent) VALUES(?,?,?,0)",
                          (phone, "__baseline__", db.now_utc_iso()))
        for sym in _symbols_for(phone):
            ck = f"{lang}:{sym}"
            if ck not in cache:
                try:
                    cache[ck] = fetcher(query_for(sym), lang, 8)
                except Exception:
                    log.exception("news fetch failed")
                    cache[ck] = []
            for item in cache[ck]:
                key = _key(sym, item["title"])
                material = is_material(item["title"])
                with db.conn() as c:
                    if c.execute("SELECT 1 FROM news_seen WHERE phone=? AND key=?", (phone, key)).fetchone():
                        continue
                    send = material and not first_run and sent_today < MAX_ALERTS_PER_DAY
                    c.execute("INSERT INTO news_seen(phone,key,seen_at,sent) VALUES(?,?,?,?)",
                              (phone, key, db.now_utc_iso(), 1 if send else 0))
                if send:
                    src = f" ({item['source']})" if item["source"] else ""
                    jobs.enqueue("send", phone, {"text": f"📰 {sym}: {item['title']}{src}",
                                                 "proactive": True, "respect_quiet": True},
                                 dedup_key=f"news:{phone}:{key}")
                    sent_today += 1
                    queued += 1
    return queued


def prune(days: int = 30) -> None:
    from datetime import timedelta
    cutoff = db.to_iso(db.now_utc() - timedelta(days=days))
    with db.conn() as c:
        c.execute("DELETE FROM news_seen WHERE seen_at < ?", (cutoff,))
