---
name: timing-planner
description: Help the user plan entry and exit conditions for a trade idea: the levels, the triggers and the invalidation point, written before the trade. Use when asked when to get in or out. Plans conditions; never gives orders.
---

# Timing Planner

There is no perfect time to buy or sell, and nobody can promise one. This skill turns "when?" into a written plan the user can test.

## Procedure
1. Get the symbol and run `price_levels` (trend, RSI, ATR, swings, 52-week distance).
2. Write an **entry condition** in observable terms: "daily close above X on volume above average", "pullback to the 50-day average holding above Y". Never "when it feels right".
3. Write the **invalidation point**: the price at which the idea is wrong (use a swing low or 2 x ATR). Say what that costs per share.
4. Write **exit conditions** for both outcomes: a target (swing high or 3 x ATR) and a time stop (if nothing happens in N days).
5. Run `position_risk` with those levels, then `scenario` for the same stop and target.
6. Offer a reminder or price alert at the entry level instead of watching the screen.

## Output shape
Condition -> level -> what it costs if wrong -> what it earns if right -> how many days to give it.

## Guardrails
- Present it as a plan to test, with the numbers' assumptions stated (costs, delayed data).
- Never say "now is the time". Never promise "maximum profit".
- If the stop is too wide for the user's risk budget, say the trade does not fit, and show the smaller size instead.
