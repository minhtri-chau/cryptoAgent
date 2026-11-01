# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Crypto Intelligence Agent: an AI-first research agent for the cryptocurrency market.
- Bitcoin first, then altcoins.
- Horizons: short-term (1–7 days), swing (1–8 weeks) and long-term (3–24 months).
- Uses technical analysis, historical analogs and strategy validation.
- Gives evidence-backed buy, sell, hold, wait and no-trade recommendations, recorded in a prediction ledger that measures whether any of it works.

[docs/architecture.md](docs/architecture.md) holds the approved architecture, the phase plan and the decision log. The external data budget is **$0**. [docs/data-sources.md](docs/data-sources.md) records what each provider allows; read it before adding or changing a collector, and don't assume data exists that it doesn't confirm.

At the end of each phase, commit and add an annotated tag (`v0.N`).

## Commands

Run from the repo root on Windows, using the project venv. `py` launches Python 3.8, so create the venv with the Python 3.11 interpreter (`python` on PATH):

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m cryptoagent init-db      # create data/cryptoagent.db and apply pending migrations
.venv/Scripts/python -m cryptoagent db-status
.venv/Scripts/python -m pytest
```

v0.1 commands:
- `sources`
- `collect coinbase|bitstamp|coinmetrics [--markets BTC/USD,...]`: backfills to listing on first run, then extends to the last complete UTC day
- `quality [--no-run] [--market SOURCE:SYMBOL]`
- `markets [--source] [--status]`
- `candles SOURCE:SYMBOL [--last N]`

v0.2 commands:
- `indicators SOURCE:SYMBOL [--as-of YYYY-MM-DD] [--last N]`
- `regime [--market] [--as-of]`
- `predict add --market --horizon --prob-up --by --rationale [--evidence JSON]`: records a judgment
- `predict baselines`
- `predictions list|score`
- `evaluate`
- `report` (writes `reports/YYYY-MM-DD.md`)

v0.3 commands:
- `indicators|patterns SOURCE:SYMBOL [--timeframe 1d|1w] [--as-of]`
- `backtest --market --pattern [--when "[MARKET] FIELD OP VALUE" ...] --hold N [--stop-atr K] [--target-atr M] --period train|validation|test --family --hypothesis --by [--trades]`: every run is recorded as a trial
- `trials list [--family] | families | show ID`: `show` prints every stored result, so any reported number can be traced

v0.4 commands:
- `collect coinbase-1h`: hourly BTC/ETH
- `indicators|patterns --timeframe 1h|4h|1d|1w`
- `scan [--as-of] [--sort FIELD] [--pattern NAME] [--limit N]`
- `recommend add --market --horizon --do buy|sell|hold|wait|no_trade --entry --invalidation [--stop-atr] [--target-atr] --risks --limitations [--evidence] [--prediction] [--report] --by`
- `recommendations list|score`
- `evaluate` resolves both predictions and recommendations

v0.5 commands:
- `analogs search SOURCE:SYMBOL [--as-of] [--horizon 30] [--k 15] [--brief]`
- `analogs evaluate --market --horizon --k --period --family --hypothesis --by`: always recorded as a trial

v0.7 commands:
- `collect defillama`
- `fundamentals SYMBOL [--as-of]`
- `note add --topic --subject --status fact|hypothesis --text [--confidence] --cite url:|trial:|prediction:|claim: [--quote] [--review-after] [--supersedes] --by agent|user`
- `note list [--topic] [--subject] [--status] [--due] [--all]`

v0.8 commands (strategy lifecycle, `promotion-v1`):
- `strategy propose --name --market --pattern [--when] --hold [--stop-atr] [--target-atr] --rationale --by agent|user`
- `strategy check NAME`: the validation gate on stored trials of the same spec
- `strategy test NAME --by`: the sealed test, once; only after the user's `authorize-test`
- `strategy list`, `strategy paper` (daily), `strategy signals`
- **User only:** `strategy authorize-test|approve|reject|retire NAME --by user`

v0.9 commands:
- `ml evaluate --market --model logit|gbm --horizon --period --family --hypothesis --by`: always recorded as a trial

v1.0 commands:
- `digest [--since] [--ahead N]`: what changed since the last briefing
- `journal add --kind briefing|note --by agent|user --text [--report] [--evidence]`
- `journal list`

v1.1 commands:
- `call add --creator --url --published --market --type up|down|reach_above|reach_below [--target] --deadline [--quote] --by`
- `call list|score [--creator]`

v1.2: `plan SOURCE:SYMBOL [--entry] [--stop price|atr:K|swing_low] [--target price|atr:K|swing_high|r:N ...] [--horizon] [--as-of]` gives a long trade plan: levels, reward-to-risk before and after costs, historical first-touch odds (volatility-adjusted, all days and same trend), and expectancy. The `/plan` skill presents it. Base rates, not forecasts. Never advise size or tell the user to buy.

Phase A of the investment upgrade:
- `dossier COIN`: everything stored about a coin.
- `token SYM ...`: live CoinGecko market cap, FDV and supply. **Never stored**, per CoinGecko's terms, and always attributed "Data provided by CoinGecko". The key lives in `.env` as `COINGECKO_DEMO_API_KEY`.
- The `fundamental-analyst` subagent.
- **Privacy:** never store the user's holdings, entry prices or personal situation in claims, the journal, predictions or recommendations. `privacy.py` refuses obvious phrasing as a backstop.

Phase B: `valuation SYM --method multiple|reverse|relative|history` turns stated assumptions into scenario prices.
- Inputs: live CoinGecko price and supply, and DefiLlama fees or revenue × 365/30. Each can be overridden.
- **No assumption has a default.** `multiple` needs `--multiple`, `--growth`, `--years` and `--dilution`; `reverse` needs `--multiple`, `--years` and `--dilution`; `relative` needs `--peer` and `--fraction`; `history` takes `--horizon`.
- Results are labelled conditional scenarios, never forecasts.

Phase D: `compare COIN COIN ... [--horizon 365] [--no-live]` puts identical facts for 2–6 coins side by side, with the correlation of their daily returns.
- It gives no scores and no ranking.
- A zero-TVL DefiLlama family is flagged as not comparable.

Maintenance: `backup [--dir] [--keep 14]` runs nightly in the daily task. It refuses the work OneDrive.

Research workflows:
- `/advise <question>`: a conclusion-first investment research assessment (buy, hold, sell, average down, take profits, cash or coin).
  - Combines the BTC backdrop, technical-analyst with `plan`, fundamental-analyst with `token` and `valuation`, history, and a risk review.
  - It separates validated evidence, base rates and judgment, and never sizes positions.
  - Each conclusion is recorded anonymously (prediction plus recommendation); personal positions are never stored.
- `/compare <COIN> <COIN> [short|swing|long]`: the same eight criteria for every coin, each with an evidence level.
  - It ranks only when the evidence supports it; otherwise it says "no clear ranking".
  - Every coin gets the same valuation assumptions, and L1s aren't set beside DeFi protocols.
  - It records one anonymous prediction per coin.
- `/learn-from <transcript or inbox/ file>`: a creator's rules become hypotheses and tests, and their calls are scored. Never store or scrape transcripts.
- `/daily [unattended]`: the daily session. See `docs/usage.md`.
- `/research <topic or coin>`: web research saved as cited claims.
- `/opportunities [short|swing|long]`: BTC backdrop, then `altcoin-scanner`, then `/investigate` on at most 3 markets.
- `/investigate <coin> [short|swing|long]` (`.claude/skills/investigate/`). It drives subagents in `.claude/agents/`: `bitcoin-analyst`, `technical-analyst`, `quant-researcher` and `risk-reviewer`, plus `altcoin-scanner` for `/opportunities`. Every investigation records a judgment prediction and a recommendation, `no_trade` included. Reports go to `reports/research/`.

The "cryptoAgent daily collect" Windows task runs `scripts/collect_daily.cmd` (collect all → quality → evaluate → baselines → report) at 9 PM local, after the 00:00 UTC daily close, logging to `data/logs/collect.log`. Re-register it with `scripts/register_daily_task.ps1`.

Every command that reads data prints JSON. Validation errors exit with status 2. Pass `--db <path>` to run against a scratch database.

## Architecture

The layers are described in [docs/architecture.md](docs/architecture.md). In short:
- **collectors** (`src/cryptoagent/collectors/`): one module per provider, each implementing the `Collector` protocol and writing `ingest_runs` plus their data rows.
  - `exchange.py`: CCXT public candles; one class serves `coinbase` (every USD spot market) and `bitstamp` (BTC/USD from 2011).
    - Stores **completed UTC days only**.
    - Walks back until an empty page to find where history starts, committing page by page so an interrupted backfill resumes.
    - Records listing status as append-only history.
    - Rejects impossible candles into `quality_issues`.
  - `coinmetrics.py`: `PriceUSD` reference prices (CC BY-NC 4.0, attribution required), used only to cross-check closes.
- **quality** (`quality.py`): checks for gaps, outlier returns and reference mismatches. It records findings and never alters data.
- **store**: SQLite, defined by numbered SQL files in `src/cryptoagent/migrations/`. Add a new numbered file for every change; never edit one that has been applied. SQLite can't add a column with a non-constant default, so use a trigger.
- **quant** (`src/cryptoagent/quant/`): plain, tested Python. All arithmetic lives here, never in prompts.
  - `indicators.py`: TA-Lib-matching implementations; TA-Lib itself is a test-only reference. Pivots are reported on their confirmation day.
  - `regime.py`: `regime-v1` descriptive labels. Its thresholds are its definition, so changing one means a new version that the user approves.
  - `series.py`: loads candles as of a day.
  - Every indicator and regime field has a no-look-ahead test (garbage future bars must not change past values). Keep that true for anything added.
- **predictions** (`predictions.py`, migration 002): append-only ledger of P(close at as-of + h > close at as-of).
  - A trigger binds each prediction to the latest stored candle, so nothing can be back-dated.
  - Outcomes must use the stored target-day close.
  - `source` is `model` (deterministic base rates, recorded daily) or `judgment` (Claude), and the scorecard compares each against `base_rate_v1`.
- **report** (`report.py`): the deterministic daily report, including the scanner and recommendation summaries.
- **universe** (`quant/universe.py`): the point-in-time liquid universe and `scan`. The default is `universe-v2`, approved 2026-10-30; `--universe universe-v1` reproduces older scans.
  - v1 rules: a candle within the last 3 days, at least 200 days of history, and at least $1M median 30-day dollar volume.
  - v2 adds two exclusions: listed pegged, commodity and wrapped assets (`EXCLUDED_ASSETS`), and anything with 30-day volatility under 10%.
  - Delisted Coinbase markets are stored, so past scans include coins that later died (no survivorship bias). Changing a threshold means a new version that the user approves.
- **recommendations** (`recommendations.py`, migration 004): an append-only ledger bound to the latest candle; a `buy` needs an ATR stop.
  - `evaluate` replays a `buy` with the backtest engine (with costs) once the horizon day's candle exists.
  - Every action is also scored against the market's own return.
- **analogs** (`quant/analogs.py`): `analog-v1` nearest past states.
  - Candidates' whole forward window must end by the as-of day (the purge).
  - Neighbours are at least one horizon apart, so the neighbour count is the effective n.
  - Walk-forward evaluation against the base rate is recorded in the trial registry. Train evaluation on BTC lost to the base rate (trials 19–20), so the method is context only.
  - The daily task still records `analog_v1` predictions for coinbase:BTC/USD to build a forward record.
- **pooled** (`quant/pooled.py`, v0.6): `backtest --market universe:universe-v2` runs one specification on every Coinbase USD market, delisted ones included, on point-in-time member days only (`universe.member_mask`, tested to agree with `membership()`).
  - Statistics are trade-level. The CI90 resamples whole signal weeks, and `distinct_signal_weeks` is reported, because a market-wide move triggers many coins at once.
  - Every backtest result also has `windows` (by year) with a stability summary.
- **research** (`research.py`, migration 005): claims are append-only and corrections supersede.
  - A fact needs a citation that resolves: a URL, trial, prediction or claim. A hypothesis needs a confidence.
  - Prices and backtest numbers never come from the web; they come from the CLI.
- **fundamentals** (`collectors/defillama.py`, `fundamentals.py`, migration 006): daily DefiLlama snapshots only, with no back-filled history, summed per protocol family and kept for Coinbase-listed tokens.
  - Ratios (price to fees and to revenue) are computed in Python.
  - The data is stored privately as the user's accepted risk. Never republish or share the database.
- **strategies** (`strategies.py`, migration 007): the `promotion-v1` lifecycle.
  - Stages: proposed → validation gate → user-authorised sealed test → paper forward test → review → user approval.
  - Stages are append-only events. A trigger allows only listed transitions and reserves authorise, approve, reject and retire for `decided_by = 'user'`.
  - Paper trades are bound to the latest candle, one is open per market, and they are resolved by replay with costs.
  - Approved strategies' signals appear in the daily report for the user to consider; nothing is traded.
- **ml** (`quant/ml.py`): `ml-logit-v1` and `ml-gbm-v1` on the analog-v1 state plus the trend label.
  - Walk-forward with a purge: training rows' outcomes must be known by the forecast date. Scored against the base rate by Brier and recorded as trials.
  - Tests show the evaluation finds a planted signal and finds none in noise.
  - No ML output feeds a decision unless validation shows a Brier-gain CI90 above 0, and then only as a user-approved proposal.
- **digest and journal** (`digest.py`, migration 008): each session starts from `digest`, which begins at the last journal briefing, and ends with a `journal add --kind briefing`. The journal is append-only.
- **calls** (`calls.py`, migration 009): outside commentators' dated calls, anchored to the publication-day close, resolved by the market, and compared with a pre-publication base rate. Forward and historical calls are scored apart.
- **Macro (FRED):** not integrated. FRED's terms forbid storing its content without written consent (`docs/data-sources.md`).
- **timeframes:** candles are stored as `1d` and `1h` (BTC and ETH only). `4h` is built from complete hours, and `1w` from complete days. Backfill state is per market and timeframe in `market_backfill`.
- **patterns** (`quant/patterns.py`): the only setups that can be backtested.
  - Each is a function with a docstring definition, a hand-worked test and a no-look-ahead test.
  - Add new ideas here as code. Subjective chart patterns aren't allowed.
  - Conditions filter signals by any indicator or regime field of any market on the same day.
- **backtest** (`quant/backtest.py`): pure daily-bar engine.
  - Signal at close t, entry at open t+1.
  - ATR stops fill at the gap open when the price gaps through.
  - Costs on both sides, one position at a time.
  - The period end closes open trades, so no later prices are ever used.
- **trials** (`trials.py`, migration 003): every backtest is recorded, append-only, failures included, with its family and hypothesis.
  - Chronological periods are the same for every market: train to 2021-12-31, validation 2022-01-01 to 2024-06-30, test from 2024-07-01.
  - The test period is **sealed**: the database allows one run per specification, and only after validation.
  - Never run `--period test` unless the user explicitly asks.
- **reasoning**: Claude skills and subagents in `.claude/`. They read the CLI's JSON output and write back only through CLI commands, so another AI provider can drive the same CLI later.

Secrets come from `.env` (gitignored) through `load_settings().secret("NAME")`, which returns a `Secret`. Use `.reveal()` only at the point of the HTTP call, and strip keys from any logged URL.

## Hard rules

- **Never invent prices, probabilities or backtest results.** Every number must trace back to a stored row or a reproducible computation. If data is missing, say "unavailable".
- **No look-ahead.** A value computed for time t may use only data observed at or before t. Universe membership, fundamentals and listings are point-in-time.
- **Record every trial.** Every strategy variant that is backtested goes into the trial registry, failures included. Results are reported together with the number of trials run.
- **Never take the user's decisions.**
  - Never run `strategy authorize-test`, `approve`, `reject` or `retire`, and never pass `--by user` on any command. The database rejects these events from anyone but `user`, and that flag is the user's alone.
  - Proposing a strategy and running `check` and `test` is fine once authorised.
- **Strategy changes need approval.** Strategies, rules and thresholds are versioned proposals. Only the user approves them; never run an `approve` command yourself. Promotion requires out-of-sample and forward-test evidence.
- **No trading.** Never build or call order, trade, transfer or withdrawal endpoints, and never handle exchange API keys that have trading permissions. Public market data only.
- **Respect provider terms.** Don't use a source whose terms exclude this use, or that is unavailable to US persons.
- **Don't edit the database by hand.** Write through the CLI or migrations.
- **No company services.** This is a personal project. Don't use Lutron/company MCP tools, services or credentials for it.
