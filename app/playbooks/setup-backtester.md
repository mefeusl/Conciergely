---
name: setup-backtester
description: Test how a chart setup behaved historically on a stock or a market, with next-day entry, ATR stop and target, costs, and an honest sample size. Use before trusting any pattern.
---

# Setup Backtester

Rules that avoid flattering the results:
- signal at the close, entry at the NEXT day's open
- stop = entry - 2 x ATR, target = entry + 3 x ATR (adjustable), long only
- if one day touches both, the stop hits first
- a gap through the stop fills at the worse open
- 0.1% round-trip cost deducted
- no overlapping trades

## Procedure
1. Run `backtest_setup` (single symbol) or read the pooled `history_of_each_setup` from `scan_results` (whole market).
2. Report: trades, win rate, target/stop/timeout shares, average R, median R, best and worst, average days held.
3. Always state the sample size. Under 30 trades: "too few to rely on".
4. Warn about what a backtest hides: survivorship (today's list excludes delisted stocks), overfitting, regime change, data errors.
5. Compare with a plain alternative (holding the stock) when the user asks whether the pattern adds anything.

## Guardrails
- Past behaviour of a rule, not a prediction.
- Do not tune stop/target until the numbers look good; if the user does, say it is curve-fitting.
