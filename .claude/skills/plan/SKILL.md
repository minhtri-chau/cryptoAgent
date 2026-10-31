---
name: plan
description: Answer "what's the risk to reward of buying X now?" and "what would a buy and sell plan look like?" with a concrete long trade plan for one coin (entry, stop, targets, reward-to-risk) plus how often, historically, the target was hit before the stop, and the expectancy after costs. Use for any question about entries, exits, stop losses, targets or risk/reward on BTC or an altcoin.
---

# /plan <coin or SOURCE:SYMBOL> [entry] [stop] [targets] [horizon]

Give the user a trade plan whose every number comes from the CLI. This is the decision support they asked for, so be clear and concrete, and just as clear about what the numbers don't mean.

## 1. Build the plan
- Market: coins default to `coinbase:<COIN>/USD`.
- Run `.venv/Scripts/python -m cryptoagent plan MARKET` with any levels the user gave:
  - `--entry PRICE` (default: the latest close);
  - `--stop PRICE|atr:K|swing_low` (default: the last confirmed swing low, or 2 × ATR if price is already below it);
  - `--target PRICE|atr:K|swing_high|r:N`, repeatable (default: the swing high if it's above the entry, plus 2R and 3R);
  - `--horizon DAYS` (default 30): match the user's time frame, 1–7 days short, 1–8 weeks swing, longer for investing.
- For context, also run `indicators MARKET --last 1`, `patterns MARKET`, `regime` (for BTC) or `scan` (for an altcoin's row and fundamentals), and `recommendations list --status open`.

## 2. Present it
| Level | Price | From entry | ATR multiple | Reward:risk (after costs) |
|---|---|---|---|---|

Then, for each target, give the historical first-touch odds: P(target first), P(stop first), P(neither) over all days and over days in the same trend, with `approx_independent` as the honest sample size, plus the expectancy after costs in % and in R.

Explain in plain words:
- **Reward:risk** is what the levels imply. The **odds** say how often that kind of move (in ATR terms) actually played out in this coin's past.
- **Expectancy combines the two.** Near zero or negative after costs means the plan has no historical edge, however good the R:R looks.
- These are base rates that include the coin's long-run drift. They aren't a forecast, and they aren't a validated strategy. Say whether any tested setup supports an entry today (check `trials list` and `strategy list`). Usually none does.
- **Structure:** whether price is above or below the last swing low and high, the trend label, and active patterns.

## 3. Optional variants
- If the user asks "where would be a better entry?", run `plan` again with a lower `--entry`, for example at the swing low or at the SMA50 from `indicators`.
- Compare the variants in one table.
- Don't run more than 4 variants: picking the best of many is curve-fitting.

## 4. Never
- Advise position size or leverage, or tell the user to buy. Present the plan and its evidence; the decision is theirs.
- Invent levels or probabilities that didn't come from the CLI.

If the user decides to act on a plan, offer to record it with `recommend add` (using the plan's stop as `--stop-atr`), so it's scored like everything else.

End with: "Research output, not financial advice. Fees are placeholders until confirmed."
