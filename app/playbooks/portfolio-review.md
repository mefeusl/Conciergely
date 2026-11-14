---
name: portfolio-review
description: Review the user's trade ledger: positions, average cost, profit/loss per currency, concentration. Use when asked how they are doing or to check their holdings.
---

# Portfolio Review

The ledger records what the user says they did. It is not connected to a broker.

## Procedure
1. Log trades with `log_trade`; show `show_portfolio` (average-cost method, delayed quotes).
2. Report open positions with weight, unrealised %, and realised profit per currency. Never add TRY and USD together.
3. Point out concentration (one position > 30% of tracked value), currency mix, and positions with no stop or plan.
4. Offer: price alerts on key levels, a journal entry for positions without a thesis, a news alert opt-in.

## Guardrails
- Not tax advice. Say average-cost may differ from the broker or tax method.
- Do not say what to buy, sell or trim; say what the numbers show.
- Selling more than the ledger holds is refused; ask the user to check missing buys.
