---
name: market-pulse-briefing
description: Give a one-glance morning or evening briefing: USD/TRY, EUR/TRY, BIST 100, S&P 500, Nasdaq, VIX, gold, Brent, US 10-year yield, plus the user's watchlist moves. Use for 'how are markets' or a daily brief.
---

# Market Pulse Briefing

## Procedure
1. Run `market_pulse`; run `get_quote` for the user's watchlist.
2. Write the briefing in at most 8 short lines: currencies first (TRY users care), then indices, VIX (fear gauge), gold, oil, yields.
3. Add at most two lines on what moved most and a headline that may explain it (`latest_news`), labelled as possible context, not cause.
4. Mention anything unavailable instead of guessing.
5. Say the data is delayed (~15 minutes, Yahoo) and not a licensed feed.

## Guardrails
- No predictions, no advice.
- Correlation is not cause: "oil fell as yields rose" is a fact, "because" needs a source.
