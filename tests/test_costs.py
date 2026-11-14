import pytest

from app import adminlogic, config, costs, pages, plans

P = "+905551234567"


def test_estimate_prices_each_counter(monkeypatch):
    for name, v in (("COST_IN_PER_M", 3.0), ("COST_OUT_PER_M", 15.0), ("COST_VOICE", 0.01), ("COST_BRIEFING", 0.05),
                    ("COST_FLIGHT", 0.02), ("USD_TRY", 0.0), ("MONTHLY_FEE_TL", 0.0)):
        monkeypatch.setattr(config, name, v)
    out = costs.estimate({"tokens_in": 2_000_000, "tokens_out": 1_000_000, "voice": 10, "briefing": 4, "flights": 5})
    assert out["total"] == pytest.approx(6.0 + 15.0 + 0.10 + 0.20 + 0.10)
    assert [r["qty"] for r in out["rows"]] == [2_000_000, 1_000_000, 10, 4, 5]
    assert out["pct"] is None and out["tl_txt"] == ""


def test_empty_usage_is_zero():
    assert costs.estimate({})["total"] == 0 and costs.estimate({})["total_txt"] == "$0.00"


def test_tiny_amounts_are_not_shown_as_zero():
    assert costs.money(0.004) == "<$0.01"
    assert costs.money(1234.5) == "$1,234.50"


def test_lira_and_share_of_fee(monkeypatch):
    monkeypatch.setattr(config, "USD_TRY", 40.0)
    monkeypatch.setattr(config, "MONTHLY_FEE_TL", 3000.0)
    monkeypatch.setattr(config, "COST_IN_PER_M", 3.0)
    out = costs.estimate({"tokens_in": 10_000_000})  # $30 = 1200 TL
    assert out["tl_txt"] == "1,200 TL" and out["pct"] == 40


def test_unknown_or_missing_values_do_not_crash():
    assert costs.estimate({"tokens_in": None, "voice": 0})["total"] == 0


def test_admin_shows_cost_per_client_and_total(client):
    plans.record(client["phone"], "tokens_in", 1_000_000)
    plans.record(client["phone"], "tokens_out", 100_000)
    common = {"bot": "Conciergely", "csrf": "x"}
    home = pages.render("dashboard", **common, **adminlogic.dashboard())
    assert "Est. cost" in home and "Estimated cost this month, all clients" in home
    assert "$" in home
    detail = pages.render("detail", **common, **adminlogic.detail(client["id"]))
    assert "Estimated cost this month" in detail and "AI reading" in detail
