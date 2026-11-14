---
name: chart-scanner
description: Scan a list of stocks for named chart setups (breakout, oversold bounce, golden cross, squeeze breakout, pullback in an uptrend) and report each hit with how that pattern behaved historically. Use when asked what setups are forming, to scan the market, or to find stocks to study.
---

# Chart Scanner

Goal: find stocks where a named, rule-based pattern is present at the latest close, and show how reliable that pattern has been.

## Setups (rules are exact, never vague)
- **breakout**: close above the highest high of the last 20 days on volume >= 1.5x the 20-day average.
- **oversold_bounce**: RSI(14) crosses back above 30 while price is above the 200-day average.
- **golden_cross**: 50-day average crosses above the 200-day average.
- **squeeze_breakout**: close above the upper Bollinger band right after the bands were near their narrowest in 6 months.
- **pullback_uptrend**: 50-day above 200-day, a green day that touched the 50-day average, RSI 40-55.

## Procedure
1. Use the latest scan results if the tool `scan_results` exists; otherwise compute the setups from daily bars the user supplies. Never invent bars or prices.
2. For every hit give: symbol, setup, last close, and the setup's history (number of past trades, win rate, average R). Always state the number of trades.
3. If the history has fewer than 30 trades, say it is too small to rely on.
4. Rank by data quality (larger history, fresher data), not by excitement.
5. End with what to check next: `price_levels`, `backtest_setup` on that symbol, `latest_news`.

## Guardrails
- A hit is a pattern, not a recommendation. Never say buy, sell, or "looks like it will go up".
- Say when data is delayed (Yahoo is ~15 minutes) or stale.
- Thousands of charts is a claim to avoid: say exactly how many symbols were scanned.
