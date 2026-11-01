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

The data is collected every night at 9 PM by the scheduled task "cryptoAgent daily collect". It needs no AI. It also records the base-rate predictions and checks the hypothesis monitors. If the PC was asleep, the task runs when it wakes, and `/daily` notices any gap.

## Learning from the channels you follow

The goal is to let analysts suggest **which questions to investigate**, never to adopt their conclusions.

1. **Get the transcript yourself,** for example from YouTube's "Show transcript" panel. Paste it into the chat, or save it as a text file in `inbox/transcripts/`, which is never committed. The agent never downloads from YouTube.
2. **Type `/learn-from`** and give the channel name, the video URL and the **upload date**. Without the URL and date, the agent can analyse the video but won't record anything, because the date decides what counts as a forward prediction.
3. **The agent sorts everything it hears into six types:**
   - **Concepts** (ways of thinking) are kept as frameworks and never become rules.
   - **Historical observations** ("lows always come after the midterms") are checked against our own data, e.g. with `cycles`.
   - **Testable hypotheses** are written neutrally into the **hypothesis registry**. A repeat by the same creator counts as another mention, not as confirmation.
   - **Dated predictions:** exact calls ("ETH to $4k by March") are scored. Conditional ones become **scenarios** with resolution criteria the agent writes; they're never counted toward the creator's score.
   - **Opinions** get one line in the report.
   - **Unverifiable claims** are listed and never stored as facts.
4. **Testing:**
   - Hypotheses the tools can express are tested **blind**. The quant researcher only sees the idea, not who said it, and must compare it with a simpler explanation.
   - A risk reviewer checks that the report doesn't overstate or misquote anyone.
5. **Forward evidence:** a source's history is in-sample for them, so the agent adds **monitors**. Whenever an idea's signal fires, the nightly task records a dated prediction, which is later scored against the base rate.
6. **Where to look later:**
   - The daily report's "Outside ideas under watch" and "Outside commentators" sections.
   - `hypothesis list`, `hypothesis show ID`, `concept list`, `scenario list` and `call score`.
   - The reports in `reports/research/learn-*.md`.
7. **Judge mainly on forward evidence.** Old calls and backtests flatter whoever you happen to follow today, and most cycle patterns rest on 3–4 past cycles.

## Commands you'll use most

| Want | Say or type |
|---|---|
| Daily update | `/daily` |
| Best opportunities today | `/opportunities` (or `/opportunities long`) |
| Should I buy / hold / sell / average down? | `/advise <your question>`, e.g. "Is AAVE worth buying today?" or "I bought SOL at $180, should I average down?" |
| Which coin is the better investment? | `/compare ETH SOL` (add `short`, `swing` or `long`; the default is long, 12 months) |
| Risk to reward and entry/exit plan | `/plan BTC` (or "what's the risk to reward of buying BTC now?") |
| Deep dive on one coin | `/investigate SOL swing` |
| Background research | `/research <topic or coin>` |
| Learn from a video or article | `/learn-from` (paste the transcript) |
| Commentators' scorecard | ask "how are the creators doing?", or run `call score` |
| Ideas learned from videos and their status | ask "what's in the hypothesis registry?", or run `hypothesis list` and `monitor list` |
| Cycle history: peaks, lows, midterm years | ask "how long did past bear markets last?", or run `cycles bitstamp:BTC/USD` |
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

## Backups

The nightly task finishes with `backup`. It makes a consistent, integrity-checked copy of the whole database in `%USERPROFILE%\Backups\cryptoAgent` and keeps the latest 14.
- **Local copies only protect against a corrupted or deleted database, not against losing this PC.** For that, set `CRYPTOAGENT_BACKUP_DIR` in `.env` to a personal cloud folder or an external drive.
- **The work OneDrive is refused**, because this is a personal project.
- **Keep the backups private**, because they contain DefiLlama data held for personal use.

The code itself is on GitHub, in a private repository. The database isn't in git.

## What's never done

- No trades, orders or exchange keys.
- No invented prices, probabilities or backtest results.
- No sharing of the database. DefiLlama data is stored under a personal-use accepted risk.

Everything here is research output, not financial advice.
