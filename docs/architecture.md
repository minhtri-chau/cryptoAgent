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
| v0.5 | Analog engine (state vectors, purged forward distributions, MAE/MFE, effective n); macro data (FRED, once its terms are verified) |
| v0.6 | Walk-forward evaluation across many windows; hourly data for the wider universe if needed (Parquet/DuckDB) |
| v0.7 | Fundamentals (DefiLlama) and research knowledge base |
| v0.8 | Strategy improvement loop: proposal → trials → holdout → paper forward test → user approval |
| v0.9 | ML baselines, compared against the simple models on the same validation |
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
- **Universe `universe-v1`** (`quant/universe.py`). These are point-in-time rules over trailing data only, so a past `--as-of` reproduces what was knowable then, including coins that were later delisted.
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
