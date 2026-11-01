# cryptoAgent

A personal, Claude-driven research agent for the cryptocurrency market. Bitcoin comes first, then liquid Coinbase altcoins. It works at three horizons: short term (1–7 days), swing (1–8 weeks) and long term (3–24 months).

It doesn't trade. It collects public market data, tests ideas on history, and gives evidence-backed research answers. It records every judgment so that it can measure whether any of them work.

> Research output, not financial advice. No orders, no exchange keys, no position sizing.

## What it can do

| Ask | How |
|---|---|
| A daily briefing: what changed, what resolved, and whether anything deserves attention | `/daily` |
| "Should I buy, hold, sell or average down on X?" | `/advise <question>`: a conclusion-first assessment combining technicals, fundamentals, scenario valuation and history, challenged by a risk reviewer |
| "X or Y?" | `/compare ETH SOL [long]`: the same eight criteria for every coin. It ranks them only when the evidence supports a ranking |
| "What's the risk to reward of buying X now?" | `/plan BTC`: levels, reward-to-risk, and how often the target was historically hit before the stop |
| The best opportunities today | `/opportunities`: the BTC backdrop, a scan of the liquid universe, and up to 3 investigations |
| A deep dive on one coin | `/investigate SOL swing` |
| Background research | `/research <topic or coin>`: saved as cited facts and labelled hypotheses |
| Learn from a YouTube analyst | `/learn-from`: their ideas become neutral hypotheses that are tested blind, and their calls are scored. It never adopts their conclusions |

## How it stays honest

- **Every number comes from stored data or a reproducible computation.** Python does the arithmetic; Claude reasons over the CLI's JSON output.
- **No look-ahead:** every indicator and pattern has a test that corrupts future data and checks nothing changes.
- **Every backtest is recorded** as a trial, failures included. History is split into train, validation, and a sealed test period that each specification can look at once, and only with the user's authorisation.
- **Predictions and recommendations** are append-only and bound to the latest stored candle, so nothing can be back-dated. They're scored against plain base rates.
- **Strategies** pass a fixed promotion gate (validation, the sealed test, a paper forward test) before the user, and only the user, can approve them.
- **Outside analysts' ideas** go into a hypothesis registry:
  - Independent support counts distinct creators, not repeats.
  - The tester never sees the source.
  - Forward evidence comes from monitors that record dated predictions whenever an idea's signal fires.
- **Privacy:** the user's holdings and entry prices are never stored.

## Data ($0 budget)

- **Candles:** Coinbase and Bitstamp public candles (CCXT), daily for every USD market and hourly for BTC and ETH.
- **Reference prices:** Coin Metrics Community, for cross-checks.
- **Fundamentals:** DefiLlama daily snapshots, stored privately.
- **Market cap and supply:** live CoinGecko Demo (never stored, per its terms).

See [docs/data-sources.md](docs/data-sources.md) for what each provider allows.

## Getting started

Windows, Python 3.11:

```
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m cryptoagent init-db
.venv/Scripts/python -m cryptoagent collect coinbase
.venv/Scripts/python -m pytest
```

Then open Claude Code in this folder and type `/daily`. The nightly collection task is registered with `scripts/register_daily_task.ps1`.

## Documentation

- [docs/usage.md](docs/usage.md): how to use it day to day, the decisions only you make, and backups.
- [CLAUDE.md](CLAUDE.md): the full command reference and hard rules. Claude Code loads it in every session.
- [docs/architecture.md](docs/architecture.md): the design, the decision log, and what each version added.
- [docs/data-sources.md](docs/data-sources.md): provider terms and what's stored.

## What's not in this repository

The database (`data/`), the reports (`reports/`), the transcripts (`inbox/`) and secrets (`.env`) stay local. Provider terms and copyright restrict sharing them, and they hold personal research history. The nightly `backup` protects them; point `CRYPTOAGENT_BACKUP_DIR` at a personal cloud folder or an external drive.
