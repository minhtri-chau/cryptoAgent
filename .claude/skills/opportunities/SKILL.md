---
name: opportunities
description: Analyze the crypto market today, find the most promising Bitcoin and altcoin opportunities for a horizon, investigate the best few with historical evidence, and record a recommendation for each. Use for requests like "what are the best opportunities today" or "scan the market".
---

# /opportunities [short | swing | long] [max investigations, default 3]

You are the lead researcher. This skill chains the specialists. Every number comes from CLI output (`.venv/Scripts/python -m cryptoagent ...`). A day with nothing worth doing is a valid, and common, result.

## 1. Data check
- Run `report` and read today's file in `reports/`.
- If the latest daily candle isn't yesterday (UTC), say so first.

## 2. Backdrop and scan (run in parallel)
- **bitcoin-analyst:** "Assess BTC's current state for an opportunity search at the HORIZON horizon."
- **altcoin-scanner:** "Scan the universe and shortlist at most five candidates for the HORIZON horizon."

## 3. Choose what to investigate
- Pick at most the requested number of markets, three by default, from:
  - the scanner's shortlist;
  - BTC itself, if the bitcoin-analyst sees an active setup;
  - ETH, if it's on the list.
- Explain the choice in a line each.
- If the BTC backdrop argues strongly against new longs, you may investigate fewer markets. Say so; don't skip the step silently.

## 4. Investigate
Run the `/investigate` workflow for each chosen market at the same horizon. Each one ends with:
- its own report in `reports/research/`;
- a recorded judgment prediction;
- a recorded recommendation.

Run them one after another, and keep each investigation's trial budget at most 8 runs, so the multiple-testing count stays visible and bounded.

## 5. Summary report
Write `reports/research/YYYY-MM-DD-opportunities.md` with these sections:
1. **Backdrop:** BTC state in a few lines.
2. **Universe and breadth:** from the scanner.
3. **Investigated:** a table of market, action, horizon, the key trial IDs, recommendation ID, prediction ID and report link.
4. **Not investigated:** the shortlisted coins you skipped and why.
5. **Trial count:** the trials run today in total (`trials families`), and the multiple-testing caveat.
6. **Track record:** the current `recommendations score` and `predictions score` (judgment rows), with their sample-size warnings.

End with: "Research output, not financial advice. Fees are placeholders until confirmed."

Tell the user the actions in one short list, plus the single most important caveat, and the summary path.
