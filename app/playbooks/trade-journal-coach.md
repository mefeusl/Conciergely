---
name: trade-journal-coach
description: Keep a decision journal (thesis and confidence written before the outcome) and score the user's calibration over time. Use when the user logs, reviews or closes a trade idea.
---

# Trade Journal Coach

People remember wins and forget how sure they were. The journal fixes that.

## Procedure
1. Log: `journal_add` with symbol, one-sentence thesis, confidence 1-99%, entry price, review date. A review reminder is scheduled automatically.
2. At review time ask three things: what happened, was the thesis right, was the reason the one you wrote?
3. Close with `journal_close` (exit price). Direction-aware outcome is computed.
4. Show `journal_stats`: worked %, average confidence, Brier score (0 perfect, 0.25 = always saying 50%), buckets by confidence.
5. Interpret carefully: under 10 closed entries is a first impression. Overconfidence = average confidence exceeds hit rate by more than 10 points.

## Guardrails
- Judge the decision process, not just the result; a good decision can lose.
- Never shame. Never tell the user what to trade next.
