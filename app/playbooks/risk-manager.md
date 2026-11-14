---
name: risk-manager
description: Calculate position size, reward:risk, break-even win rate and expectancy for a planned trade, and say what the numbers imply. Use whenever the user gives an entry, stop and target or asks whether a trade is worth the risk.
---

# Risk Manager

Goal: put numbers on a trade before money moves.

## Inputs
Entry, stop, target (long or short). Optional: estimated win probability (%), account size, risk per trade (%).

## Procedure
1. Run `position_risk`. Report: reward:risk, break-even win rate, units that keep the loss at the chosen % of the account.
2. If a win probability is given: expectancy in R = p x RR - (1 - p). Verdict words describe arithmetic only:
   - favourable: expectancy >= +0.25 R
   - marginal: between 0 and +0.25 R
   - unfavourable: zero or below
3. Show the margin over break-even in percentage points ("you need 25%; your estimate is 40%").
4. Suggest a size cap: half-Kelly, never above 2% of the account. Remind that Kelly is very sensitive to a guessed probability.
5. Include costs. A 0.1% round trip changes tight trades more than people expect.
6. Where the probability comes from matters: a backtest with few trades or a simulation is weaker than it looks. Say which it is.

## Guardrails
- "Worth it" is a statement about arithmetic under assumptions, never about the future.
- Warn if the position value exceeds the account, or if a gap could skip the stop.
- Never tell the user to take or skip the trade.
