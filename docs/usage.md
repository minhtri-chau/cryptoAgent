# Using the crypto research agent

## Every day (about 5 minutes)

1. **Open Claude Code in this folder** (`C:\Users\mchau\Claude Project\cryptoAgent`). Use a fresh session each day: it starts with clean context and loads the project rules, skills and specialist subagents automatically.
2. **Type `/daily`** (or just "good morning, what's new?"). The agent then:
   - reads `digest`, a factual summary of what changed since the last briefing;
   - fixes stale data if the nightly task was missed;
   - scores any predictions and recommendations that resolved, against the base rates;
   - summarises today's market from the deterministic report;
   - checks open calls against their invalidation conditions;
   - decides whether anything deserves a full investigation. Usually nothing does, and it says so.
   - writes a journal entry and lists any **decisions waiting for you**.
3. **Ask follow-ups in plain language**, for example:
   - "Why no trade on SOL?"
   - "What would make ETH a buy?"
   - "Research the upcoming ARB unlock" (this runs `/research`).
   - "Investigate HYPE at the swing horizon" (this runs `/investigate`).

The data is collected every night at 9 PM by the scheduled task "cryptoAgent daily collect". It needs no AI. If the PC was asleep, the task runs when it wakes, and `/daily` notices any gap.

## Commands you'll use most

| Want | Say or type |
|---|---|
| Daily update | `/daily` |
| Best opportunities today | `/opportunities` (or `/opportunities long`) |
| Deep dive on one coin | `/investigate SOL swing` |
| Background research | `/research <topic or coin>` |
| Read today's report | open `reports/YYYY-MM-DD.md` |
| See every past call and how it scored | ask "show the track record", or run `predictions score` and `recommendations score` |

## Decisions only you make

The database itself refuses these from anyone but you, and the agent is instructed never to run them:
- **Authorising a strategy's one-time sealed test:** `strategy authorize-test NAME --by user`.
- **Approving, rejecting or retiring a strategy:** `strategy approve|reject|retire NAME --by user`.
- **Changing any versioned rule:** the universe, the promotion thresholds, regime or analogue definitions, fees.

Fees are still placeholders (0.6% + 0.2% slippage per side) until you confirm your Coinbase Advanced tier.

## Optional: unattended daily briefing (off by default, untested)

`scripts/daily_claude.cmd` runs `/daily unattended` through Claude Code's headless mode (`claude -p`):
- **Account:** it uses your Claude subscription login (no API key). Each run counts towards your usage limits.
- **What it may do:** use the project CLI, read files, write under `reports/` and use the project's subagents.
- **What it doesn't do:** ask questions, or investigate more than one market.

It needs the Claude Code command-line tool installed (`claude --version` must work), which isn't the case on this PC today. Run it by hand once and read `data/logs/daily-claude-latest.json` before scheduling it. Since you're happy to check in daily, the interactive `/daily` is the recommended way.

## What's never done

- No trades, orders or exchange keys.
- No invented prices, probabilities or backtest results.
- No sharing of the database. DefiLlama data is stored under a personal-use accepted risk.

Everything here is research output, not financial advice.
