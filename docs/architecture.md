# Architecture and phased plan

Approved by the user on 2026-10-30. Changes to this plan are proposed and approved like any other strategy change.

## Mission

An AI-first cryptocurrency research agent:
- **Market analysis:** Bitcoin first, then altcoins.
- **Horizons:** short-term (1–7 days), swing (1–8 weeks) and long-term (3–24 months).
- **Analysis methods:** technical analysis, historical analogs and validated strategies.
- **Output:** evidence-backed buy, sell, hold, wait and no-trade recommendations.
- **Accountability:** a prediction ledger that measures whether any of it has real predictive value.

There is no automated trading: the user approves and executes every financial action.

## Decisions

| Date | Decision |
|---|---|
| 2026-10-30 | Own repository (`cryptoAgent`), separate from tcgAgent. |
| 2026-10-30 | Copy and adapt tcgAgent's proven patterns (numbered SQL migrations, append-only tables enforced by triggers, JSON CLI, collector protocol, user-only approvals). No shared library. |
| 2026-10-30 | The user is in the US. Providers that are unavailable to US persons are excluded. |
| 2026-10-30 | Start from exchange data. Nothing is imported from the earlier Crypto Asymmetry Engine; its ideas (e.g. 200-day EMA deviation bands at −37.5/−60/−75%) enter only as hypotheses to test. |
| 2026-10-30 | Phases re-ordered to the user's "Crypto Intelligence Agent 2.0" notes: v0.3 becomes the first working trading researcher (backtests, patterns and specialist subagents together). Altcoin scanning, hourly/4h data and macro data move to v0.4–v0.5. Hourly data starts with BTC and ETH only. FRED terms must be verified before any macro data is used. |
| 2026-10-30 | **universe-v2 approved by the user** and made the default. It is v1 plus two exclusions: a fixed list of assets that track something else (fiat stablecoins USDT, DAI, PYUSD, EURC, GUSD, PAX, BUSD, USD1, USDS; commodity token PAXG; wrapped or staked WBTC, CBETH, LSETH, MSOL, JITOSOL), and a backstop that excludes anything with 30-day volatility under 10%. Reason: USDT and PAXG were in the 2026-10-30 scan, and past universes contained DAI, WBTC and CBETH. v1 stays callable (`scan --universe universe-v1`). |
| 2026-10-30 | **analog-v1 did not beat the base rate.** In train (bitstamp:BTC/USD), Brier skill was −4.8% at 30 days (trial 19, 111 dates) and −5.3% at 90 days (trial 20, 36 dates); both CIs include 0. Validation was not run. Analogues stay as context in reports and as forward-tracked `analog_v1` predictions, but they aren't used for decisions. |
| 2026-10-30 | **FRED not used yet.** Its terms forbid storing, caching or archiving FRED content without the Bank's prior written consent. Personal-use downloading is allowed, but this project stores data by design. Macro integration waits on the user's decision (see `docs/data-sources.md`). |
| 2026-10-31 | **First pooled test (trial 21):** oversold 20-day breakdowns with BTC above its EMA200, across universe-v2, in train. 81 trades but only 23 distinct signal weeks; week-clustered CI90 (−6.0%, +12.6%); returns after the signal below drift; positive in 2 of 6 years. Not supported, so validation was not run. |
| 2026-10-31 | **ML baselines lost to the base rate.** On bitstamp:BTC/USD train, Brier skill was −11.0% for ml-logit-v1 at 30 days (trial 22) and −6.3% at 90 days (trial 23); ml-gbm-v1 scored −18.5% at 30 days (trial 24, CI90 entirely below 0: significantly worse) and −35.3% at 90 days (trial 25). The more flexible the model, the worse it did. Validation was not run, and ML output is not used. The same evaluation finds a planted signal in tests, so this is a finding about the data, not a blind spot in the method. |
| 2026-10-31 | **promotion-v1 thresholds approved by the user** as written in the v0.8 design (validation: 30+ trades, CI90 low > 0, positive at double costs, beats drift, positive in at least half the years, train mean > 0; sealed test: 10+ trades, positive, positive at double costs; paper: 90+ days, 10+ resolved trades, positive after costs). |
| 2026-10-31 | **User decision: store DefiLlama data privately, as an accepted risk.** DefiLlama's terms (24 June 2025) grant a "personal, non-commercial" licence to the official public API but don't address local storage. Clause 8(1) forbids "copy the materials"; the other bans are mirroring "on another server", republishing and commercial use; clause 14 sets liquidated damages of up to USD 100,000 per violation. Conditions: the database stays private and is never republished or shared, at most one polite collection per day, and every row keeps its source and collection time. |
| 2026-10-30 | **User decision: skip macro data for now.** No FRED collector or other macro source. To revisit: written consent from FRED, display-only fetching, or another source with storage-friendly terms. |

## Principles

1. **Arithmetic lives in Python, never in prompts.**
   - Every number in a report or recommendation traces back to a stored row or a reproducible computation.
   - Claude reads the CLI's JSON output and writes back only through CLI commands.
   - The quant engine has no LLM dependency, so another provider can later drive the same CLI.
2. **Point-in-time correctness.**
   - Every row carries `observed_at` (event time) and `collected_at` (when we recorded it).
   - Features at time t use only data at or before t.
   - Universe membership is stored as of each date.
   - Tests feed in corrupted future bars and assert that nothing computed at t changes.
3. **Overfitting is the main risk.**
   - Every tested strategy variant goes into an append-only trial registry, failures included.
   - Results are judged against the number of trials run (deflated Sharpe or an equivalent haircut).
   - A recent holdout stays sealed until a strategy is a candidate for promotion.
   - Promotion needs a forward paper test on data that did not exist when the strategy was registered, plus the user's approval.
4. **Honest sample sizes.**
   - Overlapping forward windows are not independent, so we report effective sample size.
   - Long-horizon evidence (≈4 halving cycles) is labelled thin and descriptive.
5. **Costs are part of every result.**
   - Fees and slippage are always modelled.
   - Every strategy is compared against these benchmarks: BTC buy-and-hold, DCA and a 200-day trend filter.
6. **Claude's own judgment is measured.**
   - Predictions carry `source = model | judgment` and are scored separately, so we learn whether reasoning adds value beyond the quant baseline.

## Layers

```
collectors/   one module per provider; writes ingest_runs, candles, market metadata, quality issues
store         SQLite + numbered migrations (src/cryptoagent/migrations/)
quant/        deterministic, tested Python
  indicators/ trend, momentum, volatility, volume, structure (pivots, support/resistance)
  regime/     rule-based BTC regime and cycle state
  analogs/    similarity search with purging and embargo; forward distributions, MAE/MFE
  backtest/   daily bars: signal at close t, fill at open t+1, fees and slippage
  validation/ walk-forward, purged CV, trial registry, deflated Sharpe, benchmarks
  universe/   point-in-time universe, liquidity filters, relative strength
  evaluation/ prediction scoring, calibration, drawdowns
reports/      deterministic daily report (no AI)
cli           every reading command prints JSON; validation errors exit with status 2
.claude/      skills and subagents (reasoning only)
```

### Deterministic vs Claude

| Deterministic Python | Claude |
|---|---|
| Ingestion, deduplication, quality checks | Choosing which analyses answer a question |
| Indicators, pattern definitions, regime labels | Combining conflicting signals into a view |
| Analog search, forward-return distributions, confidence intervals | Writing hypotheses as testable strategy specs |
| Backtests, walk-forward, cost model, benchmarks | Questioning weak evidence (skeptic) |
| Base-rate probabilities conditional on state | Writing recommendations from tool output |
| Stop and target levels from defined methods | Fundamentals and news research saved as cited claims |
| Prediction scoring, calibration, scorecard | Proposing strategy changes (versioned, user-approved) |
| Daily collection and report | Interpreting the report |

### Agents (introduced when there is tool output for them to use)

- **Skills (v0.4):**
  - `/analyze-market`: the orchestrator.
  - `/recommend`
  - `/predict`
  - `/research`
- **Subagents (v0.3):**
  - `bitcoin-analyst`
  - `technical-analyst`
  - `quant-researcher`: records every trial.
  - `risk-reviewer`: gets fresh context and is given the evidence, not the conclusion. It attacks recommendations for look-ahead bias, small samples, unmodelled costs and dependence on a single regime.
- **Later subagent:** `altcoin-scanner` (v0.4).

## Schema (target; built incrementally)

| Table(s) | Contents |
|---|---|
| `assets`, `venues` | Assets and the venues they trade on. |
| `markets` | One per venue, base and quote. Records `listed_at` and `delisted_at`. |
| `market_status_history` | Append-only listing-status history. |
| `candles` | Primary key `(market_id, timeframe, ts)`, `WITHOUT ROWID`. Records OHLCV, `ingest_run_id` and `collected_at`. |
| `ingest_runs`, `quality_issues` | Collection runs and the data problems found in them. |
| `universe_snapshots` | Universe membership as of each date. |
| `fundamental_obs` | Point-in-time snapshots; values from providers get revised. |
| `strategies`, `strategy_versions`, `approvals` | Strategy versions are append-only. Approvals have `decided_by = 'user'`. |
| `trials` | Append-only, failures included. |
| `predictions`, `outcomes` | Append-only. Predictions carry `source = model \| judgment`. |
| `recommendations` | Append-only, with structured fields: entry, invalidation, stop and exit methods, risk/reward, evidence references, risks, limitations. `no_trade` is a valid action. |
| `claims`, `citations` | The research knowledge base. |

## Phases

| Phase | Scope |
|---|---|
| v0.1 | Foundation and data: CLI, migrations, Coinbase daily candles (BTC and the current USD universe), long BTC history, quality checks, scheduled daily collect, `docs/data-sources.md` |
| v0.2 | Indicator library with reference tests; rule-based BTC regime and cycle state; deterministic daily report; prediction ledger and scoring (starts the forward-test clock) |
| v0.3 | First working trading researcher: weekly candles, explicit patterns, backtest engine (costs, chronological splits, sealed test period), trial registry including failures, subagents (bitcoin-analyst, technical-analyst, quant-researcher, risk-reviewer), `/investigate` skill and structured report |
| v0.4 | Altcoin scanner (point-in-time universe, liquidity filters, relative strength vs BTC, delisting handling); recommendation ledger; 1h/4h data for BTC and ETH |
| v0.5 | Analog engine (state vectors, purged forward distributions, MAE/MFE, effective n). Macro data deferred by user decision. |
| v0.6 | Stability by year in every backtest; pooled cross-market backtests over the point-in-time universe, with week-clustered CIs. Walk-forward optimisation and hourly data for the wider universe were deliberately not built (see the v0.6 design). |
| v0.7 | Research knowledge base and DefiLlama fundamentals (daily point-in-time snapshots) |
| v0.8 | Strategy improvement loop: proposal → validation gate → user-authorised sealed test → paper forward test → user approval |
| v0.9 | ML baselines (logistic, gradient boosting), walk-forward evaluated against the base rate as trials |
| v1.0 | Automated model interface (Claude API or another provider) |

At the end of each phase, commit and add an annotated tag (`v0.N`).

## v0.1 acceptance criteria

1. `python -m pip install -e ".[dev]"` works on Python 3.11+ in a fresh venv. `pytest` passes offline, with recorded fixtures and no network.
2. `cryptoagent init-db` creates the database and applies numbered migrations. Running it again changes nothing.
3. `cryptoagent collect coinbase` loads daily candles from listing for BTC-USD and every Coinbase USD market that is online now, and extends them on later runs. It pages around the per-request limit and resumes after an interruption.
4. A verified long-history source supplies BTC back to at least 2013 (Bitstamp: from 2011-08-18). Its overlap with Coinbase is reconciled, and closes that differ by more than a set tolerance are recorded as quality issues.
5. Running `collect` twice adds no duplicate rows. Every run records an `ingest_runs` row.
6. Quality checks detect and record missing days, OHLC inconsistencies, non-positive prices and outlier returns. Tests deliberately feed in each kind of corruption.
7. Rows carry `collected_at`. Market listing and status history is stored.
8. `cryptoagent db-status` and `cryptoagent quality` print JSON.
9. A Windows scheduled task runs the daily collect without Claude and writes a log.
10. `docs/data-sources.md` records each provider's terms, limits, history depth and known gaps, with links. Nothing goes in unverified.
11. CLAUDE.md is in place with the hard rules.
12. The phase ends with a commit and an annotated `v0.1` tag.

Out of scope for v0.1: indicators, backtests, recommendations, subagents and ML.

## v0.2 design

- **Indicators** (`quant/indicators.py`): pure functions over a daily OHLCV frame.
  - SMA, EMA, RSI, MACD, ATR, Bollinger bands, ADX, OBV, rate of change, realised volatility, drawdown from all-time high, deviation from a moving average, and confirmed pivot highs and lows.
  - A pivot is only known k bars after it happens, so it is reported on its confirmation day, never back-dated.
  - TA-Lib is a test-only reference. Our implementations are plain pandas, so the formula is readable and owned.
- **BTC series:** regime and cycle analysis use `bitstamp:BTC/USD` (one venue from 2011-08-18, no splice). Coinbase BTC/USD is the tradable series that predictions are made and scored on.
- **Regime `regime-v1`** (`quant/regime.py`): descriptive labels, not trade rules. Thresholds are explicit constants, and any change to them is a new version.
  - Trend: close vs SMA200 and the sign of SMA200's 20-day change → `uptrend` / `downtrend` / `transition`.
  - Volatility: 30-day realised volatility percentile within all prior history → `low` (<33rd) / `normal` / `high` (>67th).
  - Drawdown from the all-time high, and deviation from EMA200, including whether the −37.5/−60/−75% bands are reached.
  - Halving cycle: days since the last halving and the cycle number. Halvings at blocks 210,000/420,000/630,000/840,000 were checked against mempool.space block timestamps.
- **Prediction ledger** (migration 002): append-only `predictions` and `outcomes`.
  - A prediction is "probability that the close at as-of + h days is above the as-of close".
  - The as-of candle is always the latest stored complete candle when the prediction is made, so predictions cannot be back-dated.
  - `source = model | judgment`.
  - A close exactly equal to the as-of close counts as not up.
- **Baseline models**, recorded daily for `coinbase:BTC/USD` at 7/30/90/365 days, which starts the forward-test clock:
  - `base_rate_v1`: the share of all past windows of the same length that ended up, using only windows that ended on or before the as-of day.
  - `base_rate_trend_v1`: the same, restricted to windows that started in the same `regime-v1` trend label.
- **Scorecard** for each source, model and horizon:
  - Brier score, and Brier skill score vs `base_rate_v1` made on the same day.
  - Calibration bins and hit rate.
  - Resolved count, and an approximate independent count (non-overlapping windows), with a warning below 30.
- **Daily report** (`reports/YYYY-MM-DD.md`), deterministic and with no AI:
  - Data freshness and quality.
  - BTC indicators and regime.
  - Today's model predictions.
  - The scorecard.

## v0.2 acceptance criteria

1. Every indicator that TA-Lib also has matches it after warm-up within 1e-6 relative. The others are checked against hand-worked values.
2. Every indicator and regime field passes a causality test: values up to t don't change when bars after t are removed or replaced with garbage.
3. `cryptoagent indicators SOURCE:SYMBOL` and `cryptoagent regime` print JSON for the latest day or for `--as-of` a past date, using only data available then.
4. Predictions and outcomes are append-only, enforced by triggers. Predictions bind to the latest complete candle, and `evaluate` resolves them exactly at the horizon, never before.
5. `predictions score` prints the scorecard with sample sizes and small-sample warnings.
6. The daily task runs collect → quality → evaluate → baseline predictions → report, with no AI.
7. The phase ends with a commit and an annotated `v0.2` tag.

Out of scope for v0.2: backtests, strategies, recommendations, Claude skills and altcoin screens.

## v0.3 design: the first working trading researcher

- **Weekly candles:** built from stored daily candles as ISO weeks (Monday start). A week is included only once its Sunday is stored.
- **Patterns** (`quant/patterns.py`): explicit setups, each true or false at the close of day t and computed from bars up to t only.
  - `breakout_20`, `breakout_55`: close above the highest high of the previous n days (Donchian).
  - `breakdown_20`: close below the lowest low of the previous 20 days.
  - `pullback_sma50`: uptrend structure (close > SMA200, SMA50 > SMA200). The day's low comes within 2% of SMA50, or below it, and the close is back above it.
  - `squeeze`: Bollinger bandwidth in the lowest 10% of the previous 365 days.
  - `higher_highs_lows`: the last two confirmed pivot highs and the last two confirmed pivot lows are both rising.
  - `oversold_uptrend`: RSI 14 below 30 while the close is above SMA200.
  - `ema200_band_37`, `ema200_band_60`: close at least 37.5% / 60% below EMA200. This is the Crypto Asymmetry Engine hypothesis.
  - Anything not on this list has to be added as code with tests before it can be backtested. Subjective chart patterns aren't allowed.
- **Conditions:** a signal can be filtered by any indicator or regime field of any market on the same day, e.g. "only when `bitstamp:BTC/USD` `sma50_change20` > 0". This lets hypotheses like "altcoin breakouts work better when BTC's 50-day average is rising" be tested without code changes.
- **Backtest engine** (`quant/backtest.py`): long-only spot, one position at a time per test.
  - Entry: a signal at the close of t buys at the open of t+1.
  - Exit: the first of a time exit (hold N days, sold at the close), a stop at entry − k × ATR14 (filled at the stop, or at the open if the price gaps through it), or an optional target at entry + m × ATR14.
  - Costs: a fee and slippage on each side. The default is 0.6% + 0.2%, flagged `costs_confirmed: false` until the user confirms their fee tier. Every result also shows zero-cost and double-cost versions.
  - Output:
    - trade count, win rate, mean and median trade return, with a bootstrap 90% confidence interval for the mean;
    - profit factor, worst trade, MAE/MFE;
    - exposure, maximum drawdown of the strategy's equity;
    - a buy-and-hold benchmark over the same period (a same-exposure benchmark is not built yet);
    - the forward return after signals compared with all days.
- **Chronological splits**, the same dates for every market so BTC conditions can't leak across assets:
  - train: up to 2021-12-31;
  - validation: 2022-01-01 to 2024-06-30;
  - test: 2024-07-01 onwards, **sealed**.
  - A specification can be run on test only after it has a validation trial, and only once. The database enforces this.
- **Trial registry** (migration 003): every backtest run is recorded, append-only, failures included, with its hypothesis, the full specification and its hash, period, costs and results. Results show how many trials have been run in total and in this hypothesis family, so a good-looking result can be weighed against how many attempts produced it.
- **Subagents** (`.claude/agents/`) read the CLI's JSON and never compute numbers themselves:
  - `bitcoin-analyst`: regime, cycle and BTC context.
  - `technical-analyst`: indicators, structure and active patterns, across daily and weekly.
  - `quant-researcher`: turns hypotheses into specifications and runs train/validation backtests.
  - `risk-reviewer`: starts fresh and is given the evidence, not the conclusion. It attacks the thesis.
- **`/investigate <market>`** (`.claude/skills/investigate/`) orchestrates them. It writes a structured report to `reports/research/` and may record a `judgment` prediction.

## v0.3 acceptance criteria

1. Weekly candles are built only from complete weeks, with a test.
2. Every pattern has a hand-worked test and passes the no-look-ahead test.
3. The backtest engine is tested for next-open entry, cost arithmetic, stop fills including gaps, no overlapping positions, and no trade opened without a next bar.
4. The test period can't be run twice or before validation for the same specification, enforced in the database. Trials are append-only.
5. `cryptoagent patterns`, `backtest` and `trials` print JSON, and `backtest` records a trial every time it runs.
6. The four subagents and `/investigate` exist, and one demo investigation produces a report whose every number traces back to a CLI output.
7. The phase ends with a commit and an annotated `v0.3` tag.

## v0.4 design: opportunity discovery and accountability

- **Survivorship-free history.**
  - Coinbase still serves candles for delisted products (verified 2026-10-30), so the collector also backfills every *delisted* USD market once. It starts from the market's last candle, which it fetches without a start date.
  - A market's effective delisting date is its last candle. Status history only starts from our first run.
- **Universe `universe-v1`** (`quant/universe.py`; superseded by `universe-v2`, see the decision log). These are point-in-time rules over trailing data only, so a past `--as-of` reproduces what was knowable then, including coins that were later delisted.
  - Coinbase USD markets with a candle within the last 3 days of the as-of date.
  - At least 200 days of history.
  - Median daily dollar volume (close × volume) over the last 30 days of at least $1,000,000.
  - The thresholds are the definition: changing one means a new version that the user approves.
- **Scanner** (`scan`). For every market in the universe it reports:
  - trend and volatility;
  - returns relative to BTC over 30 and 90 days, as (1 + asset) / (1 + BTC) − 1;
  - distance from SMA200, drawdown, RSI and ATR%;
  - median dollar volume;
  - active patterns, and the trials recorded for that market.
  - It also gives counts of how many markets each filter removed.
  - It ranks nothing by itself beyond a sort key that the caller chooses. Choosing candidates is reasoning, done by the `altcoin-scanner` subagent.
- **Recommendation ledger** (migration 004). Append-only `recommendations`, bound by a trigger to the latest stored daily candle, like predictions.
  - Fields: action (`buy`/`sell`/`hold`/`wait`/`no_trade`), horizon in days, entry and invalidation conditions, stop and target as ATR multiples, risks, limitations, evidence JSON, an optional linked prediction, and the report path.
  - A `buy` must have a stop.
  - `evaluate` resolves a recommendation mechanically once its horizon day's candle exists:
    - a `buy` is simulated with the backtest engine: next-open entry, stop and target, time exit, costs;
    - every recommendation also records the market's own close-to-close return over the horizon, which is the benchmark for `buy` and the outcome for `sell`/`hold`/`wait`/`no_trade`.
- **Hourly data for BTC and ETH.** These come from Coinbase 1h candles, from listing. 4h candles are built from complete 1h candles, because Coinbase has no 4h granularity.
  - Backfill state moves to a per-timeframe table, `market_backfill`.
  - Indicators and patterns accept `--timeframe 1h|4h`.
  - Backtests stay on daily bars in v0.4.
- **Subagent `altcoin-scanner`** and skill **`/opportunities`**:
  - BTC backdrop;
  - then a scanner shortlist;
  - then `/investigate` on at most three candidates, each ending in a recorded recommendation and prediction, including `no_trade`.

## v0.4 acceptance criteria

1. Delisted Coinbase USD markets have their full history stored. A test shows that a past `scan --as-of` includes a market that was later delisted.
2. The universe filter uses trailing data only, which a no-look-ahead test checks.
3. `scan` prints JSON with the universe, the filter counts and the per-market metrics.
4. Recommendations are append-only and bound to the latest candle, and a `buy` needs a stop. `evaluate` resolves them with the backtest engine, and `recommendations score` summarises them by action, with sample sizes.
5. BTC and ETH have 1h candles from listing, and 4h candles come only from complete 1h candles. Indicators and patterns run on 1h and 4h.
6. The `altcoin-scanner` subagent and `/opportunities` exist, and `/investigate` records a recommendation.
7. The daily task collects 1h data and the report includes a scanner section. The phase ends with a commit; the tag waits for the terms review.

## v0.5 design: historical analogues and macro context

- **Analog engine `analog-v1`** (`quant/analogs.py`). The question it answers: when did this market last look like it does today, and what happened next?
  - **State vector** for day t, built from indicators that use bars up to t only:
    - returns over 7, 30 and 90 days;
    - deviation from EMA200;
    - SMA200 20-day slope;
    - log of 30-day volatility;
    - RSI 14;
    - drawdown from the all-time high.
  - **Point-in-time.** Searching as of day D uses only candles up to D:
    - features are z-scored with means and standard deviations from the candidate days only;
    - a candidate day s is eligible only if its whole forward window has ended by D (s + h ≤ D). This is the purge, so no outcome after D is ever used.
  - **Neighbours.** The k closest days by Euclidean distance, equal weights, chosen greedily so that no two are within h days of each other. Neighbours from the same episode aren't counted twice.
  - **Effective sample size.** The neighbour count is reported as the number of distinct episodes, never the raw count of days.
  - **Output:**
    - the neighbour dates, distances and states;
    - forward return quantiles (10/25/50/75/90), mean and share up, with bootstrap CI90s;
    - MAE and MFE over each forward window;
    - the same statistics for every eligible day (the unconditional base rate);
    - the regime label of each neighbour compared with today's.
  - Changing a feature, the distance or the selection rule makes a new version.
- **Validation against the simpler method** (`analogs evaluate`). On dates spaced h days apart within a period, it records P(up) from `analog-v1` and from the base rate, each computed only from data before the date, and scores both with Brier. Every evaluation is a trial in the registry (spec `{"method": "analog-v1", ...}`), and the same sealed-test rule applies. The analog method is used for decisions only where it beats the base rate on validation.
- **Forward tracking.** The daily task also records `analog_v1` predictions for `coinbase:BTC/USD` at 30 and 90 days, so the method builds a forward record alongside the base rates.
- **Macro data** (FRED; only series whose terms are verified, see `docs/data-sources.md`):
  - Daily observations are stored with an `available_on` date: the observation date plus the series' publication lag. Anything that uses macro data as of day D sees only values available by D.
  - Exposed through `macro` (CLI) and a macro section in the report.
  - Using macro data in backtest conditions and analogue features is v0.6, once it has been checked here.
- **Subagents.**
  - `bitcoin-analyst` adds analogues and macro context.
  - `quant-researcher` can evaluate analogues.

## v0.5 acceptance criteria

1. Analog search as of D gives identical results when bars after D are removed or replaced with garbage (no-look-ahead test). No eligible neighbour's forward window ends after D.
2. Neighbours are at least h days apart, and the effective sample size is reported. Hand-made series with known look-alike episodes return those episodes.
3. `analogs evaluate` scores analog-v1 against the base rate on non-overlapping dates, records a trial each time, and the test period stays sealed.
4. Macro series are collected only after their terms are verified, and they are stored with `available_on`. A test shows a value isn't visible before its availability date.
5. `cryptoagent analogs`, `analogs evaluate` and `macro` print JSON. The report includes analogues for BTC and the macro context.
6. The daily task records `analog_v1` predictions for BTC, and the agents use the new tools. Commit; tags wait for the terms review.

## v0.6 design: robustness across time and across markets

Every opportunity investigation so far ended with samples of 0–10 trades per coin, and with results that came from a single regime. v0.6 attacks both problems without opening the sealed test period:

- **Stability across windows.** Every backtest result includes a per-calendar-year breakdown of the same fixed specification: trades, mean return, win rate, and forward returns after signals vs all days. It also includes a stability summary:
  - windows with trades;
  - the share of those windows with a positive mean;
  - the worst and best window.

  This is walk-forward in the evaluation sense: the specification never changes between windows. Walk-forward *optimisation* (re-fitting parameters on each window) is deliberately not built. We don't optimise parameters, and a parameter search would multiply the trial count.
- **Pooled cross-market backtests.** Specifications can use the market `universe:universe-v2` (or `universe:universe-v1`). The same pattern and conditions then run on every Coinbase USD market, delisted ones included, and signals count only on days when that market was a member of the universe, judged point-in-time.
  - Conditions without a market prefix refer to each market itself. Prefixed ones (e.g. BTC) are shared.
  - One position at a time per market; trades in different markets can overlap.
  - Results are trade-level: mean return, win rate, profit factor and MAE/MFE are pooled, along with forward returns after signals vs all member days.
  - **Trades cluster in time:** one BTC move can trigger 40 coins at once. The CI90 therefore comes from a bootstrap that resamples whole signal *weeks*, and the result reports the number of distinct signal weeks next to the trade count.
  - No portfolio equity curve: equal-weight portfolio construction is a strategy choice for later.
  - Pooled specifications follow the same train/validation/test rules and the same sealed test period.
- **Vectorised universe membership.** `member_mask` must agree with `membership()` on every checked day; a test checks this.
- **Deferred:** hourly data for the wider universe. Nothing backtests intraday yet.

## v0.6 acceptance criteria

1. Every backtest (single-market and pooled) reports per-year windows and the stability summary, with a hand-checked test.
2. `member_mask` matches `membership()` on sampled days for v1 and v2, including delisted markets, short history, illiquid markets and excluded assets.
3. A pooled backtest:
   - trades only on member days;
   - holds one position per market;
   - includes markets that were later delisted;
   - never uses prices after the period;
   - reports distinct signal weeks and a week-clustered CI90;
   - can run the test period only after validation, once.
4. `backtest --market universe:universe-v2` works from the CLI and is recorded as a trial.
5. The quant researcher and `/investigate` prefer a pooled test when a single coin's history is too short. One hypothesis from the 2026-10-30 opportunities run is re-tested pooled.
6. Commit; tags wait for the terms review.

## v0.7 design: knowledge base and fundamentals

- **Research knowledge base** (migration 005, `research.py`, `note add|list`, `/research` skill):
  - append-only claims with a topic and a subject (`MARKET` or a coin);
  - a fact needs a citation: a URL that was read, a trial, a prediction or another claim;
  - a hypothesis needs a confidence;
  - corrections supersede, and review dates flag claims to re-check.
- **Fundamentals** (migration 006, `collectors/defillama.py`, `fundamentals.py`), stored privately under the user's accepted-risk decision of 2026-10-31:
  - **Collection:** three requests a day. Protocols are summed per family (e.g. Aave V2 + V3), and only families whose token is a Coinbase USD market are kept.
  - **Point-in-time:** daily snapshots only, with no back-filled history, so as-of queries can't see values before they were collected.
  - **Ratios:** price to annualised fees and to annualised revenue, computed in Python.
  - **Where it shows:** `fundamentals SYMBOL` and a `fundamentals` field on each `scan` row.
  - **Not covered:** L1 chain fees (ETH, SOL) don't come through, because chains aren't protocols in `/protocols`.
  - **Later:** fundamentals in backtest conditions, once enough snapshots have accumulated.

## v0.7 acceptance criteria

1. A fact without a citation is refused, citations must resolve, and claims are append-only with superseding.
2. Fundamentals: families are summed correctly, one snapshot is stored per day, an as-of query before the first snapshot is empty, ratios are unavailable without a market cap, and rows are append-only.
3. `note`, `fundamentals` and `scan` print JSON, and the daily task collects DefiLlama.
4. `/research`, the altcoin scanner and `/investigate` use the knowledge base and fundamentals.

## v0.8 design: the strategy improvement loop

A strategy is a backtest specification (single market or pooled universe) that has earned the right to generate signals. A strategy moves through stages, each recorded as an append-only event, and only the user can approve it:

```
proposed ──check──▶ validation_passed ──(user: authorize-test)──▶ test_authorized ──test──▶ test_passed ──▶ paper ──review──▶ paper_review ──(user)──▶ approved
    │                     │                                                                  │                         │                    └──▶ rejected
    └─▶ validation_failed └───────────────────────────────────────────────────────────────── test_failed                └─ (paper gate not met yet: keeps running)
approved ──(user)──▶ retired
```

| Stage | Who | Gate (`promotion-v1`) |
|---|---|---|
| propose | Claude or the user | A spec plus its train and validation trial IDs. Each strategy name is versioned, and a change is a new version. |
| check | system | The validation trial of the same spec must have: at least 30 trades; CI90 lower bound > 0; mean > 0 at double costs; forward return after signals above all days; and a positive mean in at least half the years with trades. The train trial must exist and have a positive mean. |
| authorize-test | **user only** | The sealed test period gives each spec one look, so spending it is the user's decision. |
| test | system | Runs the sealed test trial once. It must have at least 10 trades, mean > 0, and mean > 0 at double costs. |
| paper | system, daily | On each new candle, the spec is evaluated on the latest day. A signal records a paper trade bound to that candle; the database refuses back-dated trades. One open paper trade per market per strategy. Trades are replayed with the backtest engine (next open, stop, target, time exit, costs) once their horizon has passed. |
| review | system | At least 90 days of paper trading, at least 10 resolved trades, and a mean paper return > 0 after costs. Until then the strategy stays in paper. |
| approve / reject / retire | **user only** | Recorded with `decided_by = 'user'`, which a trigger enforces. Claude never runs these. |

- **Approved strategies** show their signals in the daily report. These are signals for the user to consider; nothing is ever traded automatically.
- **The thresholds are the definition of `promotion-v1`.** Changing one makes a new version, which the user approves.
- **Multiple testing:** each check reports the number of trials in the spec's family and in total, so the user sees how much searching sits behind a pass.

## v0.8 acceptance criteria

1. Strategy versions and events are append-only.
   - Approve, reject, retire and authorize-test require `decided_by = 'user'` (a trigger).
   - Events can only follow the allowed transitions.
2. `check` passes or fails exactly on the `promotion-v1` validation gate, using stored trials of the same spec hash.
3. `test` refuses without user authorisation and runs the sealed test once.
4. Paper trades are bound to the latest candle, one is open per market, and they are resolved by replay with costs. The review gate is applied mechanically.
5. The daily task records and evaluates paper trades, and the report lists strategies by stage and the signals of approved strategies.
6. Commit. The `promotion-v1` thresholds are presented to the user for approval (approved 2026-10-31).

Notes:
- The test step needs the validation trial of the same spec hash. It's the same rule the database enforces for any test-period run.
- A delisted market's open paper trade stays unresolved until candles exist past its horizon. That never happens, so it stays open; the review gate counts only resolved trades.

## v0.9 design: machine learning, held to the same standard

The brief: don't introduce complex models before the simple baselines are reliable, and judge ML against simpler statistical methods. So v0.9 adds two models and one question: **do they beat the base rate out of sample?**

- **Models** (`quant/ml.py`). Both are deterministic, with a fixed random state.
  - `ml-logit-v1`: standardised logistic regression. It's simple and usually well calibrated.
  - `ml-gbm-v1`: a shallow histogram gradient-boosting classifier (depth 3, 100 iterations, learning rate 0.05). It's more flexible, and more likely to overfit.
- **Data:**
  - Features: the analog-v1 state (returns, EMA200 deviation, SMA200 slope, log volatility, RSI, drawdown), plus the regime-v1 trend label one-hot encoded. All are causal.
  - Target: whether the close `h` bars later is above today's.
- **Walk-forward evaluation**, the same protocol as analogues:
  - Dates are `h` bars apart inside a period.
  - At each date the model is fitted only on rows whose whole forward window ended by that date (the purge).
  - It needs at least 500 training rows, with both outcomes present.
  - P(up) is compared with the base rate of the same training rows, by Brier score, with a CI90 on the per-date Brier difference.
- **Every evaluation is a trial** (spec `{"method": "ml-logit-v1" | "ml-gbm-v1", market, horizon_days}`). Train first, validation only if train shows skill, and the sealed test only for promotion.
- **No ML output feeds a decision** until a model shows positive Brier skill with a CI90 above 0 on validation. Even then it would enter as a proposed model needing the user's approval, like a strategy.
- **Dependency:** scikit-learn.

## v0.9 acceptance criteria

1. No training row's forward window reaches past the evaluation date (a test).
2. On synthetic data with a planted, learnable relationship, the models beat the base rate. On a pure random walk they don't show significant skill. This shows the evaluation can tell signal from noise.
3. `ml evaluate` records a trial; the sealed-test rules apply.
4. BTC is evaluated in train at 30 and 90 days for both models, and the result is recorded in the decision log whatever it is.
5. The report lists every method evaluation (analogues and ML) against the base rate.

## Main risks

| Risk | Mitigation |
|---|---|
| Overfitting through repeated testing | Trial registry, holdout, forward tests |
| Small samples at long horizons | Report effective n; label thin evidence |
| Survivorship bias in altcoins | Reduced, not removed, with free data; stated in every altcoin result |
| Free tiers and terms change | Recheck `docs/data-sources.md` each phase |
| Look-ahead bias | Enforced with tests |
| Persuasive narrative without evidence | Skeptic subagent; `judgment` vs `model` scoring |

This is research, not financial advice.
