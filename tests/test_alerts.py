from datetime import datetime, timezone

import pytest

from app import alerts, db, jobs, markets

P = "+905551234567"
TODAY = "2026-10-22"


def w(**kw):
    base = {"upper": 320.0, "lower": 290.0, "move_pct": None, "state": "unknown", "pct_alert_date": ""}
    base.update(kw)
    return base


# ---------- evaluate(): pure decision logic ----------

def test_inside_limits_sends_nothing():
    assert alerts.evaluate(w(), 305, None, TODAY) == ([], "inside", "")


def test_crossing_upper_limit_alerts_once_then_stays_quiet():
    events, state, _ = alerts.evaluate(w(state="inside"), 321, None, TODAY)
    assert (events, state) == (["above"], "above")
    events, state, _ = alerts.evaluate(w(state="above"), 330, None, TODAY)
    assert (events, state) == ([], "above")


def test_crossing_lower_limit_alerts():
    events, state, _ = alerts.evaluate(w(state="inside"), 289, None, TODAY)
    assert (events, state) == (["below"], "below")


def test_limit_is_inclusive():
    assert alerts.evaluate(w(state="inside"), 320, None, TODAY)[0] == ["above"]
    assert alerts.evaluate(w(state="inside"), 290, None, TODAY)[0] == ["below"]


def test_rearms_after_returning_inside_then_alerts_again():
    _, state, _ = alerts.evaluate(w(state="above"), 310, None, TODAY)
    assert state == "inside"
    assert alerts.evaluate(w(state=state), 322, None, TODAY)[0] == ["above"]


def test_already_beyond_limit_on_first_check_alerts():
    assert alerts.evaluate(w(state="unknown"), 340, None, TODAY)[0] == ["above"]


def test_jump_from_above_straight_to_below_alerts_below():
    events, state, _ = alerts.evaluate(w(state="above"), 280, None, TODAY)
    assert (events, state) == (["below"], "below")


def test_only_one_limit_set():
    only_upper = w(lower=None)
    assert alerts.evaluate(only_upper, 10, None, TODAY)[1] == "inside"
    assert alerts.evaluate(only_upper, 400, None, TODAY)[0] == ["above"]


def test_daily_move_alert_fires_once_per_day_in_both_directions():
    pct = w(upper=None, lower=None, move_pct=3.0)
    events, state, date = alerts.evaluate(pct, 100, 3.4, TODAY)
    assert (events, date) == (["move"], TODAY)
    assert alerts.evaluate({**pct, "pct_alert_date": TODAY}, 100, 4.0, TODAY)[0] == []
    assert alerts.evaluate({**pct, "pct_alert_date": TODAY}, 100, 4.0, "2026-10-23")[0] == ["move"]
    assert alerts.evaluate(pct, 100, -3.1, TODAY)[0] == ["move"]
    assert alerts.evaluate(pct, 100, 2.9, TODAY)[0] == []
    assert alerts.evaluate(pct, 100, None, TODAY)[0] == []


# ---------- market hours ----------

def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


@pytest.mark.parametrize("symbol,when,expected", [
    ("THYAO.IS", utc(2026, 10, 21, 9, 0), True),    # Wed 12:00 Istanbul
    ("THYAO.IS", utc(2026, 10, 21, 6, 0), False),   # Wed 09:00 Istanbul, before the open
    ("THYAO.IS", utc(2026, 10, 21, 15, 30), False), # Wed 18:30 Istanbul, after the close
    ("THYAO.IS", utc(2026, 10, 24, 9, 0), False),   # Saturday
    ("NVDA", utc(2026, 10, 21, 15, 0), True),       # Wed 11:00 New York
    ("NVDA", utc(2026, 10, 21, 12, 0), False),      # Wed 08:00 New York
    ("NVDA", utc(2026, 10, 24, 15, 0), False),      # Saturday
    ("BTC-USD", utc(2026, 10, 25, 3, 0), True),     # crypto trades on Sunday
    ("USDTRY=X", utc(2026, 10, 25, 3, 0), True),
])
def test_market_hours(symbol, when, expected):
    assert markets.market_is_open(symbol, when) is expected


# ---------- run_cycle(): decisions -> queued messages ----------

@pytest.fixture
def watcher(monkeypatch):
    db.upsert_client(P, name="X")
    db.set_client_fields(P, lang="en", onboarding_done=1)
    monkeypatch.setattr(markets, "market_is_open", lambda s, n=None: True)
    price = {"value": 305.0}
    monkeypatch.setattr(markets, "get_quote", lambda s: {
        "symbol": s, "price": price["value"], "previous_close": 300.0,
        "change_pct": (price["value"] - 300.0) / 3.0, "currency": "TRY"})
    db.add_watch(P, "THYAO.IS", 320.0, 290.0, None)
    return price


def queued_texts():
    out = []
    while (j := jobs.claim()):
        out.append(j["payload"])
    return out


def test_cycle_alerts_once_per_crossing(watcher):
    assert alerts.run_cycle() == 0
    watcher["value"] = 321.5
    assert alerts.run_cycle() == 1
    assert alerts.run_cycle() == 0  # still above: no repeat
    texts = queued_texts()
    assert len(texts) == 1 and texts[0]["proactive"] is True
    assert "upper limit" in texts[0]["text"] and "THYAO.IS" in texts[0]["text"] and "321.50" in texts[0]["text"]
    watcher["value"] = 300.0
    assert alerts.run_cycle() == 0  # back inside: re-armed silently
    watcher["value"] = 322.0
    assert alerts.run_cycle() == 1


def test_cycle_below_limit_in_turkish(watcher):
    db.set_client_fields(P, lang="tr")
    watcher["value"] = 288.0
    assert alerts.run_cycle() == 1
    assert "alt limitinizin altına" in queued_texts()[0]["text"]


def test_cycle_skips_closed_markets_and_missing_quotes(watcher, monkeypatch):
    watcher["value"] = 400.0
    monkeypatch.setattr(markets, "market_is_open", lambda s, n=None: False)
    assert alerts.run_cycle() == 0
    monkeypatch.setattr(markets, "market_is_open", lambda s, n=None: True)
    monkeypatch.setattr(markets, "get_quote", lambda s: None)
    assert alerts.run_cycle() == 0


def test_one_failing_symbol_does_not_block_others(watcher, monkeypatch):
    db.add_watch(P, "GARAN.IS", 100.0, None, None)

    def flaky(symbol):
        if symbol == "THYAO.IS":
            raise RuntimeError("yahoo down")
        return {"symbol": symbol, "price": 150.0, "previous_close": 140.0, "change_pct": 7.0, "currency": "TRY"}

    monkeypatch.setattr(markets, "get_quote", flaky)
    assert alerts.run_cycle() == 1
    assert "GARAN.IS" in queued_texts()[0]["text"]


def test_paused_client_gets_no_alerts(watcher):
    db.set_client_fields(P, active=0)
    watcher["value"] = 400.0
    assert alerts.run_cycle() == 0


def test_daily_move_message(watcher):
    db.add_watch(P, "THYAO.IS", None, None, 3.0)
    watcher["value"] = 330.0  # +10% vs previous close 300
    assert alerts.run_cycle() == 1
    text = queued_texts()[0]["text"]
    assert "threshold" in text and "+10.00%" in text
