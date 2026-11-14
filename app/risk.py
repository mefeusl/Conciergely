"""Risk arithmetic for a planned trade. Pure maths on numbers the client supplies; no market data.

Nothing here says "buy" or "skip". It says what the numbers imply under the stated assumptions, and the
verdict words (favourable / marginal / unfavourable) describe the arithmetic only.
"""
from __future__ import annotations

import math


def _pos(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def position_size(account: float, risk_pct: float, entry: float, stop: float) -> dict:
    """How many units keep the loss at `risk_pct` percent of the account if the stop is hit."""
    if not (_pos(account) and _pos(risk_pct) and _pos(entry) and _pos(stop)):
        return {"error": "account, risk_pct, entry and stop must be positive numbers"}
    if risk_pct > 100:
        return {"error": "risk_pct must be 100 or less"}
    per_unit = abs(entry - stop)
    if per_unit == 0:
        return {"error": "entry and stop are the same price"}
    risk_amount = account * risk_pct / 100.0
    units = math.floor(risk_amount / per_unit)
    cost = units * entry
    return {"units": units, "risk_amount": round(units * per_unit, 2), "risk_budget": round(risk_amount, 2),
            "position_value": round(cost, 2), "position_pct_of_account": round(cost / account * 100, 1),
            "exceeds_account": cost > account,
            "note": "units are rounded down; gaps and slippage can make a real loss larger than the stop implies"}


def assess(entry: float, stop: float, target: float, win_prob_pct: float | None = None,
           account: float | None = None, risk_pct: float | None = None, cost_pct: float = 0.1) -> dict:
    """Reward:risk, break-even win rate and (if a win probability is given) expectancy in R."""
    if not (_pos(entry) and _pos(stop) and _pos(target)):
        return {"error": "entry, stop and target must be positive numbers"}
    long = target > entry
    if long and not stop < entry:
        return {"error": "for a long trade the stop must be below the entry"}
    if not long and not (target < entry < stop):
        return {"error": "for a short trade the target must be below the entry and the stop above it"}
    risk = abs(entry - stop)
    reward = abs(target - entry)
    cost = entry * cost_pct / 100.0
    net_reward, net_risk = reward - cost, risk + cost
    if net_reward <= 0:
        return {"error": "costs are larger than the reward"}
    rr = net_reward / net_risk
    breakeven = net_risk / (net_risk + net_reward) * 100.0
    out = {"direction": "long" if long else "short", "reward_risk": round(rr, 2),
           "breakeven_win_pct": round(breakeven, 1), "cost_pct_assumed": cost_pct,
           "risk_pct_of_entry": round(risk / entry * 100, 2), "reward_pct_of_entry": round(reward / entry * 100, 2)}
    if win_prob_pct is not None:
        if not (isinstance(win_prob_pct, (int, float)) and not isinstance(win_prob_pct, bool)
                and 0 <= win_prob_pct <= 100):
            return {"error": "win_prob_pct must be between 0 and 100"}
        p = win_prob_pct / 100.0
        ev = p * rr - (1 - p)
        out["win_prob_pct"] = round(win_prob_pct, 1)
        out["expectancy_r"] = round(ev, 2)
        out["margin_over_breakeven_pts"] = round(win_prob_pct - breakeven, 1)
        out["verdict"] = verdict(ev)
        kelly = (p * rr - (1 - p)) / rr if rr > 0 else 0.0
        out["kelly_fraction_pct"] = round(max(kelly, 0.0) * 100, 1)
        out["suggested_cap_pct"] = round(min(max(kelly, 0.0) * 50, 2.0), 2)  # half-Kelly, never above 2% of the account
        out["suggested_cap_note"] = "half-Kelly capped at 2% of the account; the win probability is an estimate and Kelly is very sensitive to it"
    if account is not None and risk_pct is not None:
        sizing = position_size(account, risk_pct, entry, stop)
        out["sizing"] = sizing
    return out


def verdict(ev_r: float) -> str:
    if ev_r >= 0.25:
        return "favourable"
    if ev_r > 0:
        return "marginal"
    return "unfavourable"
