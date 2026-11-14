import math
import random

import pytest

from app import levels, risk, timemachine


def test_position_size():
    r = risk.position_size(100_000, 1, 50, 48)
    assert r["units"] == 500 and r["risk_amount"] == 1000 and r["position_value"] == 25_000
    assert not r["exceeds_account"]
    assert risk.position_size(10_000, 2, 100, 99.9)["exceeds_account"]
    for bad in [(0, 1, 50, 48), (1000, 0, 50, 48), (1000, 1, 50, 50), (1000, 101, 50, 48), (1000, 1, -5, 4),
                (float("nan"), 1, 5, 4), (True, 1, 5, 4)]:
        assert "error" in risk.position_size(*bad)
    assert risk.position_size(1000, 1, 100, 50)["units"] == 0  # budget below one unit


def test_assess_math():
    a = risk.assess(100, 98, 106, cost_pct=0)
    assert a["reward_risk"] == 3.0 and a["breakeven_win_pct"] == 25.0 and a["direction"] == "long"
    b = risk.assess(100, 98, 106, win_prob_pct=40, cost_pct=0)
    assert b["expectancy_r"] == pytest.approx(0.6, abs=0.01) and b["verdict"] == "favourable"
    assert b["margin_over_breakeven_pts"] == 15.0
    assert risk.assess(100, 98, 106, win_prob_pct=25, cost_pct=0)["verdict"] == "unfavourable" or True
    assert risk.assess(100, 98, 106, win_prob_pct=25, cost_pct=0)["expectancy_r"] == 0.0
    assert risk.assess(100, 98, 106, win_prob_pct=25, cost_pct=0)["verdict"] == "unfavourable"
    assert risk.assess(100, 98, 106, win_prob_pct=30, cost_pct=0)["verdict"] == "marginal"


def test_assess_costs_and_caps():
    free = risk.assess(100, 98, 102, cost_pct=0)
    costly = risk.assess(100, 98, 102, cost_pct=0.5)
    assert costly["reward_risk"] < free["reward_risk"] and costly["breakeven_win_pct"] > free["breakeven_win_pct"]
    big = risk.assess(100, 99, 120, win_prob_pct=90, cost_pct=0)
    assert big["suggested_cap_pct"] <= 2.0
    neg = risk.assess(100, 99, 101, win_prob_pct=10, cost_pct=0)
    assert neg["kelly_fraction_pct"] == 0.0 and neg["suggested_cap_pct"] == 0.0


def test_assess_validation_and_short():
    assert "error" in risk.assess(100, 101, 110)  # long with stop above
    assert "error" in risk.assess(100, 98, 100.01, cost_pct=1)  # costs eat the reward
    assert "error" in risk.assess(100, 98, 106, win_prob_pct=101)
    assert "error" in risk.assess(100, 98, 106, win_prob_pct=True)
    assert "error" in risk.assess(0, 98, 106)
    s = risk.assess(100, 102, 94, cost_pct=0)
    assert s["direction"] == "short" and s["reward_risk"] == 3.0
    assert "error" in risk.assess(100, 98, 94)  # short with stop below
    withsize = risk.assess(100, 98, 106, account=50_000, risk_pct=1)
    assert withsize["sizing"]["units"] == 250


def closes(n=400, seed=2, vol=0.015, drift=0.001):
    rnd = random.Random(seed)
    p, out = 100.0, []
    for _ in range(n):
        p *= 1 + rnd.gauss(drift, vol)
        out.append(p)
    return out


def test_sim_reproducible_and_bounded():
    c = closes()
    a = timemachine.simulate(c, 20, 1000, seed=1)
    b = timemachine.simulate(c, 20, 1000, seed=1)
    assert a == b
    assert timemachine.simulate(c, 20, 1000, seed=2) != a
    big = timemachine.simulate(c, 10_000, 10**9, seed=1)
    assert big["horizon_days"] == 252 and big["paths"] == 5000
    small = timemachine.simulate(c, 0, 1, seed=1)
    assert small["horizon_days"] == 1 and small["paths"] == 200


def test_sim_percentiles_ordered_and_scale_with_volatility():
    lo = timemachine.simulate(closes(vol=0.005), 20, 2000, seed=3)
    hi = timemachine.simulate(closes(vol=0.03), 20, 2000, seed=3)
    for res in (lo, hi):
        e = res["end_return_pct"]
        assert e["p5"] <= e["p25"] <= e["p50"] <= e["p75"] <= e["p95"]
        assert res["max_drawdown_pct"]["bad_case_p95"] <= res["max_drawdown_pct"]["median"] <= 0
    assert hi["end_return_pct"]["p95"] - hi["end_return_pct"]["p5"] > 3 * (lo["end_return_pct"]["p95"] - lo["end_return_pct"]["p5"])


def test_sim_demean_removes_drift():
    c = closes(drift=0.004)  # strong uptrend
    flat = timemachine.simulate(c, 60, 3000, seed=4, demean=True)
    drift = timemachine.simulate(c, 60, 3000, seed=4, demean=False)
    assert abs(flat["prob_up_pct"] - 50) < 8
    assert drift["prob_up_pct"] > 75


def test_sim_levels_probabilities_add_up():
    c = closes()
    last = c[-1]
    r = timemachine.simulate(c, 30, 3000, stop=last * 0.95, target=last * 1.10, seed=5)
    assert r["target_first_pct"] + r["stop_first_pct"] + r["neither_pct"] == pytest.approx(100, abs=0.2)
    assert r["target_first_pct"] > 0 and r["stop_first_pct"] > 0
    # tighter stop is hit more often
    tight = timemachine.simulate(c, 30, 3000, stop=last * 0.99, target=last * 1.10, seed=5)
    assert tight["stop_first_pct"] > r["stop_first_pct"]
    # symmetric levels on driftless walk: roughly even odds, expectancy near zero
    sym = timemachine.simulate(c, 60, 4000, stop=last * 0.95, target=last * 1.05, seed=6)
    assert abs(sym["target_first_pct"] - sym["stop_first_pct"]) < 8 and abs(sym["expectancy_r"]) < 0.25
    bad = timemachine.simulate(c, 30, 500, stop=last * 1.1, target=last * 1.2, seed=1)
    assert "levels_ignored" in bad and "target_first_pct" not in bad


def test_sim_too_little_data():
    assert "error" in timemachine.simulate([1, 2, 3], 10)
    assert "error" in timemachine.simulate([5.0] * 30, 10)


def bars_from(c):
    return [(f"d{i}", c[i - 1] if i else c[0], max(c[i], c[i - 1] if i else c[0]) * 1.005,
             min(c[i], c[i - 1] if i else c[0]) * 0.995, c[i], 1e6) for i in range(len(c))]


def test_levels_snapshot():
    up = [100 * (1.004 ** i) for i in range(300)]
    s = levels.snapshot(bars_from(up))
    assert s["trend"].startswith("uptrend") and s["from_52w_high_pct"] > -3
    assert s["study_levels"]["stop_2atr_below"] < s["last_close"] < s["study_levels"]["target_3atr_above"]
    down = [100 * (0.996 ** i) for i in range(300)]
    assert levels.snapshot(bars_from(down))["trend"].startswith("downtrend")
    assert "error" in levels.snapshot(bars_from(up[:30]))


def test_swings_and_levels_around_price():
    wave = [100 + 10 * math.sin(i / 6) for i in range(300)]
    s = levels.snapshot(bars_from(wave))
    assert all(x < s["last_close"] for x in s["nearby_supports"])
    assert all(x > s["last_close"] for x in s["nearby_resistances"])
    hs, ls = levels.swings([1, 2, 3, 9, 3, 2, 1, 1, 2, 3, 4], [1, 2, 3, 4, 3, 2, 0.5, 2, 3, 4, 5])
    assert 9 in hs and 0.5 in ls
