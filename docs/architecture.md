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
- **Subagents (v0.5–v0.7):**
  - `btc-regime`
  - `alt-scout`
  - `strategy-researcher`: records every trial.
  - `skeptic`: gets fresh context and is given the evidence, not the conclusion. It attacks recommendations for look-ahead bias, small samples, unmodelled costs and dependence on a single regime.

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
| v0.3 | Backtest engine, cost model, walk-forward, trial registry, benchmarks; baseline strategies including the EMA-band hypothesis |
| v0.4 | Recommendation ledger; skills; scoring of `judgment` vs `model` predictions |
| v0.5 | Analog engine (state vectors, purged forward distributions, MAE/MFE, effective n); `btc-regime` and `skeptic` subagents |
| v0.6 | Altcoins: point-in-time universe, liquidity and volatility filters, relative strength vs BTC, delisting handling; `alt-scout` |
| v0.7 | Hourly data (Parquet/DuckDB if needed), short-term horizon, fundamentals, research knowledge base |
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
