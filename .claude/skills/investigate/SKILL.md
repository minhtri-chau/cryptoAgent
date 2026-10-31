---
name: investigate
description: Investigate one cryptocurrency (e.g. ETH, SOL, or a SOURCE:SYMBOL market) using the specialist subagents and the cryptoagent CLI, test candidate setups on history, have the thesis challenged, and write a structured, evidence-backed research report with a buy, sell, hold, wait or no-trade recommendation.
---

# /investigate <coin or SOURCE:SYMBOL> [horizon: short | swing | long]

You are the lead researcher. The subagents gather and test evidence. You decide, write the report, and own every claim in it. Numbers come only from CLI output (`.venv/Scripts/python -m cryptoagent ...`); never compute or recall one. If something is missing, write "unavailable".

Horizons:
- `short`: 1–7 days.
- `swing`: 1–8 weeks. This is the default.
- `long`: 3–24 months. Backtests on daily bars are weakest here, so say so.

`sell` means exiting or reducing an existing holding: the project is long-only spot and never shorts. `no_trade` and `wait` are normal outcomes, not failures.

## 1. Set up
- Resolve the market. Coins default to `coinbase:<COIN>/USD`. Check it with `markets --source coinbase`.
- Run `report` if today's report doesn't exist yet, and check that the data is fresh: the latest candle should be yesterday (UTC). Stale data must be stated at the top of the report.

## 2. Gather (run these in parallel)
- **bitcoin-analyst:** "Assess BTC's current state for an investigation of MARKET at the HORIZON horizon."
- **technical-analyst:** "Analyse MARKET (daily and weekly) and propose up to three backtestable hypotheses suited to a HORIZON horizon."

**Context (optional, in parallel):**
- Run `fundamentals COIN` and `note list --subject COIN`.
- If something material may be unknown, such as an imminent token unlock, an upgrade, a delisting or a regulatory action, run `/research COIN` first and cite the claim IDs in the report.
- This context can argue for caution, or against a setup. It can't replace a tested setup as the reason to buy.

## 3. Choose hypotheses
- Choose at most three from the technical analyst's proposals, plus your own ideas built from the BTC context. One BTC-conditioned hypothesis is usually worth including.
- Write each one down before testing: the specification, and what result would count as support.
- Check `trials families` so you don't repeat past work blindly.

## 4. Test
Give **quant-researcher** the hypotheses, the market, a family name for each, and a budget of 8 runs. Train first, validation only for survivors, never the test period.

If the coin has little history before 2022, also ask for the pooled version of the main hypothesis (`--market universe:universe-v2`). Report it as evidence about the setup in general, not about this coin.

## 5. Draft the thesis
Base it only on the evidence gathered. Fill every field:
- **asset and timeframe**, and the horizon;
- **current conditions**, from both analysts;
- **proposed action:** `buy` / `sell` / `hold` / `wait` / `no_trade`;
- **entry conditions:** the tested pattern and conditions that must be true at a daily close;
- **invalidation conditions:** what would make the thesis wrong before the stop;
- **levels:** run `plan MARKET --stop atr:K --target ... --horizon DAYS` with the tested specification's stop and exit, and quote its reward-to-risk and historical first-touch odds;
- **stop-loss method:** e.g. entry − K × ATR14, using the K that was tested, with today's level from `indicators`;
- **profit-taking method:** the tested hold days or target;
- **risk/reward:** from tested numbers only (mean MAE/MFE, win rate, mean return with CI). If it can't be derived from tests, write "untested";
- **supporting evidence:** trial IDs, plus the command behind each number;
- **important risks;**
- **confidence limitations:** sample sizes, CIs including 0, train vs validation, costs not confirmed, number of trials run.

If no hypothesis was supported in both train and validation, the action is `wait` or `no_trade`. Say what evidence would change that.

## 6. Challenge
Give **risk-reviewer** the draft and the evidence: the raw subagent reports and trial IDs. Don't give it your reasoning.
- Apply its required changes.
- If its verdict is `does not stand`, downgrade to `wait` or `no_trade`, or explain in the report exactly why you disagree, quoting its objection.

## 7. Record and write
- **Record a judgment prediction for every investigation.** Recording only the confident ones would bias the scorecard.
  - Use the horizon in days: 7, 30, 90 or 365.
  - Choose `prob_up` relative to the base rate from `predictions list --source model`, and explain any difference larger than 0.05 in the rationale.
  ```
  .venv/Scripts/python -m cryptoagent predict add --market MARKET --horizon DAYS --prob-up P --by claude \
    --rationale "action=...; key evidence: trial #..., ...; base rate ..." \
    --evidence "{\"trials\": [..], \"report\": \"reports/research/YYYY-MM-DD-COIN.md\"}"
  ```
  This only works when the latest candle is in the database; the CLI binds the prediction to it.
- **Record the recommendation, for every investigation, including `wait` and `no_trade`.**
  - Use the thesis fields and link the prediction.
  - A `buy` must use the stop, and any target, of the tested specification. Without a tested specification, a buy isn't allowed.
  - `evaluate` later replays a `buy` with the backtest engine. Every action is compared with the market's own return over the horizon.
  ```
  .venv/Scripts/python -m cryptoagent recommend add --market MARKET --horizon DAYS --do ACTION \
    --entry "..." --invalidation "..." [--stop-atr K] [--target-atr M] --risks "..." --limitations "..." \
    --evidence "{\"trials\": [..]}" --prediction PREDICTION_ID --report reports/research/YYYY-MM-DD-COIN.md --by claude
  ```
- Write `reports/research/YYYY-MM-DD-<coin>.md` with these sections in this order:
  1. Summary: the action, and one paragraph.
  2. Data status.
  3. Bitcoin backdrop.
  4. Technical picture.
  5. Hypotheses tested: a table of the specifications, trial IDs, train vs validation results and verdicts.
  6. Thesis: every field from step 5.
  7. Risk review: the verdict, objections and how each was handled.
  8. Prediction and recommendation recorded: their IDs, P(up), the base rate, the target day, and the stop and target levels from `recommend add`.
  9. Limitations.
  10. Sources: the commands run.

  End with: "Research output, not financial advice. Fees are placeholders until confirmed."
- Tell the user the action, the two or three most important reasons and caveats, and the report path. Don't paste the whole report.
