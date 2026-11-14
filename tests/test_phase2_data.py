import json
import random

import pytest

from app import bars, db, journal, jobs, news, portfolio, pulse, scanner, setups
from tests.conftest import PHONE


# ---------------- portfolio ----------------

def test_portfolio_average_cost_and_realized(client):
    assert portfolio.add_trade(PHONE, "thyao", "buy", 100, 10, fee=5, traded_on="2026-01-02", currency="try")["ok"]
    portfolio.add_trade(PHONE, "THYAO", "buy", 100, 14, traded_on="2026-01-03", currency="TRY")
    p = portfolio.positions(PHONE)["THYAO"]
    assert p["qty"] == 200 and p["cost"] == pytest.approx(2405)  # avg 12.025
    portfolio.add_trade(PHONE, "THYAO", "sell", 50, 15, traded_on="2026-01-04", currency="TRY")
    p = portfolio.positions(PHONE)["THYAO"]
    assert p["qty"] == 150 and p["realized"] == pytest.approx(50 * (15 - 12.025))
    s = portfolio.summary(PHONE, quote_fn=lambda sym: {"price": 16.0, "currency": "TRY"})
    pos = s["positions"][0]
    assert pos["avg_cost"] == pytest.approx(12.025) and pos["unrealized"] == pytest.approx(150 * 16 - 150 * 12.025, abs=0.01)
    assert s["realized_by_currency"]["TRY"] == pytest.approx(148.75, abs=0.01)


def test_portfolio_rejects_bad_input_and_oversell(client):
    for args in [("../x", "buy", 1, 1), ("A", "hold", 1, 1), ("A", "buy", 0, 1), ("A", "buy", 1, -1),
                 ("A", "buy", float("nan"), 1), ("A", "buy", True, 1)]:
        assert "error" in portfolio.add_trade(PHONE, *args)
    assert "error" in portfolio.add_trade(PHONE, "A", "buy", 1, 1, traded_on="2999-01-01")
    assert "error" in portfolio.add_trade(PHONE, "A", "buy", 1, 1, traded_on="nope")
    assert "error" in portfolio.add_trade(PHONE, "A", "sell", 1, 1)  # nothing held
    portfolio.add_trade(PHONE, "A", "buy", 5, 1)
    assert "error" in portfolio.add_trade(PHONE, "A", "sell", 6, 1)
    assert portfolio.add_trade(PHONE, "A", "sell", 5, 2)["ok"]
    assert portfolio.summary(PHONE)["positions"] == []


def test_portfolio_isolated_between_clients_and_quote_failure(client):
    other = "+905559998877"
    db.upsert_client(other)
    portfolio.add_trade(PHONE, "A", "buy", 1, 1)
    assert portfolio.positions(other) == {}
    def boom(sym): raise RuntimeError("x")
    assert "unrealized" not in portfolio.summary(PHONE, quote_fn=boom)["positions"][0]


def test_delete_trade_guard(client):
    a = portfolio.add_trade(PHONE, "A", "buy", 5, 1, traded_on="2026-01-01")["id"]
    portfolio.add_trade(PHONE, "A", "sell", 5, 2, traded_on="2026-01-02")
    with pytest.raises(ValueError):
        portfolio.delete_trade(PHONE, a)
    assert len(portfolio.positions(PHONE)) == 1  # rolled back
    assert portfolio.delete_trade(PHONE, 9999) is False


def test_erase_client_removes_trading_data(client):
    portfolio.add_trade(PHONE, "A", "buy", 1, 1)
    journal.add(PHONE, "A", "because", 60)
    with db.conn() as c:
        c.execute("INSERT INTO news_seen(phone,key,seen_at) VALUES(?,?,?)", (PHONE, "k", db.now_utc_iso()))
    db.erase_client(PHONE)
    with db.conn() as c:
        for t in ("trades", "journal", "news_seen"):
            assert c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0


# ---------------- journal ----------------

def test_journal_add_validation_and_review_reminder(client):
    for bad in [dict(symbol="", thesis="x", confidence=50), dict(symbol="A", thesis="", confidence=50),
                dict(symbol="A", thesis="x", confidence=0), dict(symbol="A", thesis="x", confidence=100),
                dict(symbol="A", thesis="x", confidence=True), dict(symbol="A", thesis="x", confidence=50, direction="up"),
                dict(symbol="A", thesis="x", confidence=50, entry_price=-1)]:
        assert "error" in journal.add(PHONE, **bad)
    r = journal.add(PHONE, "nvda", "earnings run-up\n\n   with   spaces", 70, entry_price=100, review_days=7)
    assert r["ok"]
    assert journal.list_open(PHONE)[0]["thesis"] == "earnings run-up with spaces"
    pend = db.list_pending_reminders(PHONE)
    assert len(pend) == 1 and "NVDA" in pend[0]["text"]


def test_journal_close_long_short_and_stats(client):
    a = journal.add(PHONE, "A", "t", 80, entry_price=100)["id"]
    b = journal.add(PHONE, "B", "t", 80, direction="short", entry_price=100)["id"]
    c = journal.add(PHONE, "C", "t", 60)["id"]
    assert journal.close(PHONE, a, 110)["outcome_pct"] == 10.0
    r = journal.close(PHONE, b, 110)
    assert r["outcome_pct"] == -10.0 and r["worked"] is False
    assert "error" in journal.close(PHONE, a, 120)  # already closed
    assert "error" in journal.close(PHONE, c, 120)  # no entry price
    assert journal.set_entry_price(PHONE, c, 50) and journal.close(PHONE, c, 55)["worked"]
    assert "error" in journal.close(PHONE, 999, 5)
    assert "error" in journal.close("+905000000000", a, 5)
    s = journal.stats(PHONE)
    assert s["closed_entries"] == 3 and "warning" in s and s["worked_pct"] == pytest.approx(66.7, abs=0.1)
    brier = ((0.8 - 1) ** 2 + (0.8 - 0) ** 2 + (0.6 - 1) ** 2) / 3
    assert s["brier_score"] == pytest.approx(brier, abs=0.001)


def test_journal_overconfidence_reading(client):
    for i in range(10):
        e = journal.add(PHONE, "A", "t", 90, entry_price=100)["id"]
        journal.close(PHONE, e, 90 if i < 6 else 110)  # 4 of 10 worked
    s = journal.stats(PHONE)
    assert "overconfidence" in s["reading"] and "warning" not in s
    assert s["by_confidence"]["70%+"]["worked_pct"] == 40.0
    assert journal.stats("+905000000000")["closed_entries"] == 0


# ---------------- news ----------------

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>THYAO bilan&#231;o a&#231;&#305;klad&#305; &lt;b&gt;x&lt;/b&gt;</title><source>Bloomberg HT</source><pubDate>Tue, 20 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Hava durumu</title></item><item><title></title></item></channel></rss>"""


def test_parse_rss_and_hostile_inputs():
    items = news.parse_rss(RSS)
    assert len(items) == 2 and "<b>" not in items[0]["title"] and items[0]["published"].startswith("2026-10-20")
    assert news.parse_rss(b"<!DOCTYPE x [<!ENTITY a 'b'>]><rss/>") == []
    bomb = b"<!DOCTYPE x [<!ENTITY a 'boom'>]><rss><channel><item><title>&a;</title></item></channel></rss>"
    assert news.parse_rss(bomb) == []  # refused outright, never expanded
    assert news.parse_rss(b"<rss><channel><item><title>ok</title></item></channel></rss>")[0]["title"] == "ok"
    assert news.parse_rss(b"not xml") == []
    assert news.parse_rss(b"x" * 1_000_001) == []
    assert news.clean("a\x00b\x1bc") == "a b c"
    assert len(news.clean("x" * 999)) == 200


def test_material_filter():
    assert news.is_material("THYAO bilanço açıkladı") and news.is_material("Nvidia earnings beat")
    assert news.is_material("FDA halts trial") and not news.is_material("Hava durumu yarın güzel")
    assert not news.is_material("")


def _optin(lang="en"):
    db.upsert_client(PHONE)
    db.set_client_fields(PHONE, onboarding_done=1, news_alerts=1, lang=lang)
    db.add_watch(PHONE, "NVDA", None, None, None)


def test_news_first_run_is_silent_then_alerts_once(client):
    _optin()
    feed = [{"title": "Nvidia earnings beat estimates", "source": "X", "published": ""},
            {"title": "Nvidia fan event photos", "source": "Y", "published": ""}]
    f = lambda q, lang, n: feed
    assert news.run_cycle(f) == 0  # baseline
    feed.append({"title": "Nvidia announces acquisition of startup", "source": "Z", "published": ""})
    assert news.run_cycle(f) == 1
    assert news.run_cycle(f) == 0  # already seen
    with db.conn() as c:
        job = c.execute("SELECT payload FROM jobs WHERE kind='send'").fetchone()
    payload = json.loads(job["payload"])
    assert "acquisition" in payload["text"] and payload["respect_quiet"] and payload["proactive"]


def test_news_daily_cap_and_optin(client):
    _optin()
    feed = []
    f = lambda q, lang, n: feed
    news.run_cycle(f)
    feed.extend({"title": f"Nvidia lawsuit number {i}", "source": "", "published": ""} for i in range(8))
    assert news.run_cycle(f) == 3
    db.set_client_fields(PHONE, news_alerts=0)
    feed.append({"title": "Nvidia buyback", "source": "", "published": ""})
    assert news.run_cycle(f) == 0


def test_news_fetch_failure_is_contained(client):
    _optin()
    def boom(q, lang, n): raise RuntimeError("down")
    assert news.run_cycle(boom) == 0


def test_news_includes_portfolio_symbols(client):
    _optin()
    portfolio.add_trade(PHONE, "THYAO.IS", "buy", 1, 1)
    asked = []
    news.run_cycle(lambda q, lang, n: asked.append(q) or [])
    assert "NVDA stock" in asked and "THYAO hisse OR KAP" in asked


# ---------------- pulse ----------------

def test_pulse_handles_missing():
    def q(sym):
        if sym == "^VIX": return None
        if sym == "GC=F": raise RuntimeError("x")
        return {"price": 10.123456, "change_pct": 1.234}
    s = pulse.snapshot(q)
    assert "VIX (fear gauge)" in s["unavailable"] and "Gold" in s["unavailable"] and len(s["items"]) == 7
    assert s["items"][0]["change_pct"] == 1.23


# ---------------- scanner ----------------

def test_universe_files_are_sane():
    b, u = scanner.universe("bist"), scanner.universe("us")
    assert len(b) >= 80 and all(s.endswith(".IS") for s in b) and len(set(b)) == len(b)
    assert len(u) >= 120 and "AAPL" in u and "BRK-B" in u
    assert scanner.universe("../etc") == []


def uptrend_with_breakout(n=300, seed=1):
    rnd = random.Random(seed)
    p, out = 100.0, []
    for i in range(n):
        o = p
        p *= 1 + rnd.gauss(0.001, 0.01)
        out.append([f"2026-{i:05d}", o, max(o, p) * 1.003, min(o, p) * 0.997, p, 1e6])
    # force a breakout on last bar
    top = max(b[2] for b in out[-21:-1])
    out[-1][4] = top * 1.03
    out[-1][2] = out[-1][4] * 1.002
    out[-1][5] = 3e6
    return [tuple(b) for b in out]


def test_scan_stores_hits_stats_and_skips_stale(client, monkeypatch, tmp_path):
    fresh_date = db.now_utc().strftime("%Y-%m-%d")
    good = uptrend_with_breakout()
    good = good[:-1] + [(fresh_date,) + good[-1][1:]]
    stale = uptrend_with_breakout(seed=2)  # last date far in the past
    short = good[-50:]
    monkeypatch.setattr(scanner, "universe", lambda m: ["GOOD", "STALE", "SHORT", "MISSING"])
    monkeypatch.setattr(bars, "prefetch", lambda syms: None)
    monkeypatch.setattr(bars, "load_cached", lambda s: {"GOOD": good, "STALE": stale, "SHORT": short}.get(s))
    rep = scanner.run_scan("us")
    assert rep["scanned"] == 1
    res = scanner.results("us")
    assert any(h["symbol"] == "GOOD" and h["setup"] == "breakout" for h in res["hits"])
    assert not any(h["symbol"] in ("STALE", "SHORT", "MISSING") for h in res["hits"])
    assert "us:breakout" in res["history_of_each_setup"]
    # rescan replaces rather than duplicates
    scanner.run_scan("us")
    assert len(scanner.results("us", "breakout")["hits"]) == len([h for h in res["hits"] if h["setup"] == "breakout"])
    assert scanner.scan_age_ok("us") and not scanner.scan_age_ok("bist")
    assert scanner.results("bist")["hits"] == []


def test_results_limit_clamped(client):
    assert scanner.results(limit=10**6)["hits"] == []
    assert scanner.results(limit=-5)["hits"] == []


def test_market_of():
    assert scanner.market_of("THYAO.IS") == "bist" and scanner.market_of("AAPL") == "us"
