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

The "cryptoAgent daily collect" Windows task runs `scripts/collect_daily.cmd` (collect all → quality) at 9 PM local, after the 00:00 UTC daily close, logging to `data/logs/collect.log`. Re-register it with `scripts/register_daily_task.ps1`.

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
- **quant**: plain, tested Python. All arithmetic lives here, never in prompts.
- **reasoning**: Claude skills and subagents in `.claude/`. They read the CLI's JSON output and write back only through CLI commands, so another AI provider can drive the same CLI later.

Secrets come from `.env` (gitignored) through `load_settings().secret("NAME")`, which returns a `Secret`. Use `.reveal()` only at the point of the HTTP call, and strip keys from any logged URL.

## Hard rules

- **Never invent prices, probabilities or backtest results.** Every number must trace back to a stored row or a reproducible computation. If data is missing, say "unavailable".
- **No look-ahead.** A value computed for time t may use only data observed at or before t. Universe membership, fundamentals and listings are point-in-time.
- **Record every trial.** Every strategy variant that is backtested goes into the trial registry, failures included. Results are reported together with the number of trials run.
- **Strategy changes need approval.** Strategies, rules and thresholds are versioned proposals. Only the user approves them; never run an `approve` command yourself. Promotion requires out-of-sample and forward-test evidence.
- **No trading.** Never build or call order, trade, transfer or withdrawal endpoints, and never handle exchange API keys that have trading permissions. Public market data only.
- **Respect provider terms.** Don't use a source whose terms exclude this use, or that is unavailable to US persons.
- **Don't edit the database by hand.** Write through the CLI or migrations.
- **No company services.** This is a personal project. Don't use Lutron/company MCP tools, services or credentials for it.
