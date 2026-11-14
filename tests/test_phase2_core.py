import math
import random

import pandas as pd
import pytest

from app import backtest, bars, indicators as ind, setups


def series(n=300, seed=1, start=100.0, drift=0.0005, vol=0.015):
    rnd = random.Random(seed)
    p, out = start, []
    for i in range(n):
        o = p
        p = p * (1 + rnd.gauss(drift, vol))
        h, l = max(o, p) * (1 + abs(rnd.gauss(0, .004))), min(o, p) * (1 - abs(rnd.gauss(0, .004)))
        out.append((f"2025-{i:05d}", o, h, l, p, 1_000_000 * (1 + abs(rnd.gauss(0, .3)))))
    return out


def cols(b):
    return [x[2] for x in b], [x[3] for x in b], [x[4] for x in b]


def close(a, b, tol=1e-6):
    for x, y in zip(a, b):
        if y is None or (isinstance(y, float) and math.isnan(y)):
            assert x is None
        else:
            assert x == pytest.approx(y, rel=tol, abs=tol)


def test_sma_ema_vs_pandas():
    c = [x[4] for x in series()]
    s = pd.Series(c)
    close(ind.sma(c, 20), s.rolling(20).mean().tolist())
    # pandas ewm seeded with SMA differs; compare after warmup using adjust=False seeded manually
    e = ind.ema(c, 10)
    k, prev = 2 / 11, sum(c[:10]) / 10
    for i in range(10, len(c)):
        prev = c[i] * k + prev * (1 - k)
        assert e[i] == pytest.approx(prev)
    assert e[8] is None and e[9] == pytest.approx(sum(c[:10]) / 10)


def test_rsi_vs_pandas_wilder():
    c = [x[4] for x in series()]
    d = pd.Series(c).diff()
    up, dn = d.clip(lower=0), (-d).clip(lower=0)
    # Wilder seeded with SMA of first 14 changes
    ag, al = [None] * len(c), [None] * len(c)
    ag[14], al[14] = up[1:15].mean(), dn[1:15].mean()
    for i in range(15, len(c)):
        ag[i] = (ag[i - 1] * 13 + up[i]) / 14
        al[i] = (al[i - 1] * 13 + dn[i]) / 14
    exp = [None] * 14 + [100 - 100 / (1 + ag[i] / al[i]) for i in range(14, len(c))]
    close(ind.rsi(c, 14), exp)


def test_rsi_edges():
    assert ind.rsi([1.0] * 30)[20] == 50.0
    assert ind.rsi([float(i) for i in range(1, 40)])[30] == 100.0
    assert ind.rsi([1.0, 2.0], 14) == [None, None]


def test_atr_matches_manual():
    b = series(60)
    h, l, c = cols(b)
    a = ind.atr(h, l, c, 14)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, 60)]
    prev = sum(tr[1:15]) / 14
    assert a[14] == pytest.approx(prev) and a[13] is None
    for i in range(15, 60):
        prev = (prev * 13 + tr[i]) / 14
        assert a[i] == pytest.approx(prev)


def test_bollinger_vs_pandas():
    c = [x[4] for x in series()]
    s = pd.Series(c)
    mid, up, lo, w = ind.bollinger(c)
    sd = s.rolling(20).std(ddof=0)
    close(up, (s.rolling(20).mean() + 2 * sd).tolist())
    close(lo, (s.rolling(20).mean() - 2 * sd).tolist())


def test_clean_drops_bad_rows():
    rows = [("2025-01-01", 1, 2, 0.5, 1.5, 10), ("x", float("nan"), 1, 1, 1, 1), ("y", 1, 1, 2, 1, 1),
            ("z", 0, 1, 1, 1, 1), ("w", "a", 1, 1, 1, 1), ("v", 5, 4, 6, 5, None)]
    out = bars.clean(rows)
    assert [r[0] for r in out] == ["2025-01-01"]


def test_symbol_validation_and_fallback(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "a.db"))
    calls = []
    good = series(60)

    def dl(sym):
        calls.append(sym)
        return good if sym == "THYAO.IS" else []
    monkeypatch.setattr(bars, "_download_one", dl)
    assert bars.get_bars("../etc/passwd") is None
    assert bars.get_bars("") is None
    sym, got = bars.get_bars("thyao")
    assert sym == "THYAO.IS" and calls == ["THYAO", "THYAO.IS"] and len(got) == 60
    calls.clear()
    assert bars.get_bars("THYAO.IS")[0] == "THYAO.IS" and calls == []  # cache hit


def test_stale_cache_when_yahoo_down(tmp_path, monkeypatch):
    import os, time
    from app import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "a.db"))
    bars._write_cache("AAPL", series(60))
    p = bars._cache_path("AAPL")
    old = time.time() - 10 * 86400
    os.utime(p, (old, old))
    monkeypatch.setattr(bars, "_download_one", lambda s: [])
    assert bars.get_bars("AAPL")[0] == "AAPL"
    assert bars.get_bars("NOPE") is None


def test_prefetch_uses_bulk(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "a.db"))
    seen = []
    monkeypatch.setattr(bars, "_download", lambda syms: seen.append(list(syms)) or {s: series(60) for s in syms if s != "BAD"})
    n = bars.prefetch(["A", "B", "BAD", "bad sym"])
    assert n == 2 and seen == [["A", "B", "BAD"]]
    seen.clear()
    bars.prefetch(["A", "B"])
    assert seen == []  # cached


def mk(opens, highs, lows, closes, vols=None):
    vols = vols or [1_000_000] * len(closes)
    return [(f"d{i:04d}", opens[i], highs[i], lows[i], closes[i], vols[i]) for i in range(len(closes))]


def test_compute_lengths_and_none_warmup():
    d = setups.compute(series(250))
    for k in ("sma20", "sma50", "sma200", "rsi", "atr", "bb_upper", "bb_width"):
        assert len(d[k]) == 250
    assert d["sma200"][198] is None and d["sma200"][199] is not None


def test_breakout_detected_and_not_before():
    n = 260
    c = [100 + 0.01 * i for i in range(n)]
    o, h, l = c[:], [x + .5 for x in c], [x - .5 for x in c]
    v = [1_000_000] * n
    c[240] = max(h[220:240]) + 3
    h[240] = c[240] + .2
    v[240] = 2_000_000
    d = setups.compute(mk(o, h, l, c, v))
    assert setups.detect("breakout", d, 240)
    assert not any(setups.detect("breakout", d, i) for i in range(30, 240))
    v2 = v[:]; v2[240] = 1_000_000
    d2 = setups.compute(mk(o, h, l, c, v2))
    assert not setups.detect("breakout", d2, 240)  # no volume


def test_detect_no_lookahead():
    b = series(300, seed=5)
    full = setups.compute(b)
    for name in setups.SETUPS:
        hits_full = {i for i in range(len(b)) if setups.detect(name, full, i)}
        cut = 230
        part = setups.compute(b[:cut])
        hits_part = {i for i in range(cut) if setups.detect(name, part, i)}
        assert hits_part == {i for i in hits_full if i < cut}, name


def test_unknown_setup_and_bounds():
    d = setups.compute(series(250))
    assert not setups.detect("nope", d, 100)
    assert not setups.detect("breakout", d, 0) and not setups.detect("breakout", d, 9999)


def test_golden_cross_fires_once():
    c = [200 - 0.4 * i for i in range(250)] + [100 + 0.9 * i for i in range(250)]
    o = c[:]; h = [x + 1 for x in c]; l = [x - 1 for x in c]
    d = setups.compute(mk(o, h, l, c))
    hits = [i for i in range(500) if setups.detect("golden_cross", d, i)]
    assert len(hits) == 1


def _flat_with_signal(setup_ok_bar, after):
    """Build bars where 'breakout' fires at bar 240 then price follows `after` (list of (o,h,l,c))."""
    n = 241
    c = [100 + 0.01 * i for i in range(n)]
    o, h, l = c[:], [x + .5 for x in c], [x - .5 for x in c]
    v = [1_000_000] * n
    c[240] = max(h[220:240]) + 3; h[240] = c[240] + .2; v[240] = 2_000_000
    for (a, b, cc, dd) in after:
        o.append(a); h.append(b); l.append(cc); c.append(dd); v.append(1_000_000)
    return mk(o, h, l, c, v)


def test_backtest_target_stop_ordering_and_costs():
    base = _flat_with_signal(0, [])
    d = setups.compute(base)
    atr = d["atr"][240]
    entry = 110.0
    # day1 opens at entry, spikes above target and below stop same day -> stop first
    both = _flat_with_signal(0, [(entry, entry + 5 * atr, entry - 5 * atr, entry)] + [(entry,) * 4] * 12)
    t = backtest.run(both, "breakout", cost_pct=0)
    assert t and t[0]["outcome"] == "stop" and t[0]["r"] == pytest.approx(-1.0, abs=1e-6)
    # clean target
    tgt = _flat_with_signal(0, [(entry, entry + 3.1 * atr, entry - 0.1, entry + 3)] + [(entry,) * 4] * 12)
    t = backtest.run(tgt, "breakout", cost_pct=0)
    assert t[0]["outcome"] == "target" and t[0]["r"] == pytest.approx(1.5, abs=1e-6)
    t2 = backtest.run(tgt, "breakout", cost_pct=0.5)
    assert t2[0]["r"] < t[0]["r"]
    # gap through stop fills at open (worse than -1R)
    gap = _flat_with_signal(0, [(entry, entry, entry, entry), (entry - 4 * atr, entry - 4 * atr, entry - 4 * atr, entry - 4 * atr)] + [(entry,) * 4] * 12)
    t = backtest.run(gap, "breakout", cost_pct=0)
    assert t[0]["outcome"] == "stop" and t[0]["r"] < -1.5


def test_backtest_timeout_and_no_overlap_and_short_history():
    entry = 110.0
    flat = _flat_with_signal(0, [(entry, entry + .1, entry - .1, entry)] * 12)
    t = backtest.run(flat, "breakout", cost_pct=0)
    assert t[0]["outcome"] == "timeout" and t[0]["days"] == 10
    assert backtest.run(series(100), "breakout") == []
    assert backtest.run(series(300), "nonsense") == []


def test_summarize():
    assert backtest.summarize([])["trades"] == 0
    ts = [{"r": 1.5, "outcome": "target", "return_pct": 3, "days": 2}, {"r": -1.0, "outcome": "stop", "return_pct": -2, "days": 1}]
    s = backtest.summarize(ts)
    assert s["trades"] == 2 and s["win_rate_pct"] == 50.0 and s["avg_r"] == 0.25 and not s["reliable"]
    big = backtest.summarize(ts * 20)
    assert big["reliable"] and "too few" not in big["note"]


def test_run_many_pools():
    entry = 110.0
    a = _flat_with_signal(0, [(entry, entry + .1, entry - .1, entry)] * 12)
    out = backtest.run_many({"A": a, "B": a, "C": series(50)}, "breakout")
    assert out["trades"] == 2 and out["symbols_with_signals"] == 2


def test_backtest_skips_signals_while_a_trade_is_open():
    entry = 110.0
    # second breakout bar 3 days after the first, while the first trade (flat, 10-day window) is still open
    n = 241
    c = [100 + 0.01 * i for i in range(n)]
    o, h, l = c[:], [x + .5 for x in c], [x - .5 for x in c]
    v = [1_000_000] * n
    c[240] = max(h[220:240]) + 3; h[240] = c[240] + .2; v[240] = 2_000_000
    rows = [(entry, entry + .1, entry - .1, entry)] * 12
    for (a, b, cc, dd) in rows:
        o.append(a); h.append(b); l.append(cc); c.append(dd); v.append(1_000_000)
    c[243] = entry + 3; h[243] = c[243] + .2; o[243] = entry; l[243] = entry - .1; v[243] = 2_000_000
    data = setups.compute(mk(o, h, l, c, v))
    assert setups.detect("breakout", data, 240) and setups.detect("breakout", data, 243)
    trades = backtest.run(mk(o, h, l, c, v), "breakout", cost_pct=0)
    assert len(trades) == 1 and trades[0]["signal_date"] == "d0240"


def test_oversold_bounce_needs_a_cross_not_just_low_rsi():
    d = setups.compute(series(300, seed=9))
    i = 250
    base = dict(d)
    base["rsi"] = list(d["rsi"])
    base["sma200"] = list(d["sma200"])
    base["close"] = list(d["close"])
    base["sma200"][i] = base["close"][i] - 1  # above the 200-day
    for prev, now, expect in ((25, 31, True), (40, 35, False), (25, 28, False), (30, 31, False), (29.9, 30, True)):
        base["rsi"][i - 1], base["rsi"][i] = prev, now
        assert setups.detect("oversold_bounce", base, i) is expect, (prev, now)
    base["rsi"][i - 1], base["rsi"][i] = 25, 31
    base["sma200"][i] = base["close"][i] + 1  # below the 200-day: no signal
    assert not setups.detect("oversold_bounce", base, i)
