---
name: learn-from
description: Learn from a YouTube transcript, article or post by a crypto commentator. Extracts their testable rules (timing, cycles, patterns, fundamentals, macro) as cited hypotheses, records their dated calls so they are scored against base rates, and backtests the rules the project can express. Use when the user pastes a transcript, points at a file in inbox/, or says "learn from this video/channel".
---

# /learn-from <pasted text, or a file in inbox/transcripts/> [creator name] [video URL] [publish date]

Goal: turn someone's analysis into **evidence about whether it works**, not into beliefs. The creator's ideas are hypotheses until the project's own tests say otherwise. Their confidence, following and past hits are not evidence.

## 0. Ground rules
- **Source:** the user provides the transcript, by pasting it or saving it in `inbox/transcripts/`, which is gitignored. Never download from YouTube or other sites; their terms forbid automated scraping.
- **Copyright:** never save the transcript itself in the database or in reports. Save at most a 25-word quote per claim or call, plus the URL with its timestamp (`&t=754s`).
- **Required facts:** you need the creator, the URL and the **publish date**. If any is missing, ask the user. Don't guess the date: it anchors every call.
- Every number in your output comes from the CLI (`.venv/Scripts/python -m cryptoagent ...`) or is quoted from the transcript and labelled as the creator's claim.

## 1. Read and sort
Read the whole text. Sort every substantive statement into exactly one bucket:

| Bucket | What it is | Example |
|---|---|---|
| **A. Testable rule** | A condition and an expected outcome, stated precisely enough to compute | "When BTC closes below the 200-week MA it has always bottomed within 6 months" |
| **B. Dated call** | A specific market, direction or target, and a deadline (explicit, or clearly implied, like "by year end") | "ETH reaches $4,000 by March" |
| **C. Context claim** | A fact about the world that can be checked against a source | "The ARB unlock is on 16 March" |
| **D. Opinion** | Not testable as stated | "This feels like 2017" |

## 2. Record
- **A. Testable rules** become hypotheses, one per rule:
  ```
  note add --by agent --topic <bitcoin|altcoins|market_structure|macro|fundamentals|strategy> --subject <COIN or MARKET> \
    --status hypothesis --confidence 0.2 --text "<creator> claims: <the rule, stated precisely>" \
    --cite url:<url with timestamp> --quote "<25 words or fewer>"
  ```
  Confidence starts low (about 0.2). It only rises after the project's own tests support the rule.
- **B. Dated calls** are recorded so they're scored:
  ```
  call add --creator "<name>" --url <url with timestamp> --published YYYY-MM-DD --market coinbase:<COIN>/USD \
    --type up|down|reach_above|reach_below [--target PRICE] --deadline YYYY-MM-DD --quote "<call>" --by agent
  ```
  - A video older than 7 days is recorded as `historical` and scored separately, so that's fine.
  - Skip calls without a deadline you can state honestly. Say which ones you skipped.
  - If the publish date has no stored candle (a coin listed later, or before our data), say so and skip the call.
- **C. Context claims:** check the important ones with `/research`, then save each as a cited fact or a hypothesis.
- **D. Opinions:** don't record them individually. Summarise the creator's overall stance in one line of the report.

## 3. Test what the project can express
For each testable rule:
1. **Map it** onto the project's defined `patterns` and `--when` conditions (any indicator or regime field, on any market).
2. **Rules that need something the project lacks:** list them under "Needs new definitions" in the report, with what each would need: a new pattern, the weekly timeframe in backtests, macro data (unavailable by user decision), or on-chain data (not collected). Don't approximate a rule into something it doesn't say.
3. **Run the mappable rules through quant-researcher**, at most 4 trials per transcript. Use family `learn:<creator>`, and state the hypothesis as the creator's rule.
   - Rules about many coins ("altcoins pump when…") run pooled: `--market universe:universe-v2`.
   - Train first. Validation only if train supports the rule.
4. Cycle and halving rules about BTC rest on about 4 cycles. Say that the sample can't confirm them, whatever the backtest shows.

## 4. Report
Write `reports/research/learn-YYYY-MM-DD-<creator>.md` with these sections:
1. **Source:** creator, URL, publish date, and a one-line overall stance.
2. **Testable rules:** each with its claim ID, how it was mapped, its trial IDs and verdict, or "not tested: needs …".
3. **Calls recorded:** call IDs, type, target, deadline, and the base rate when it resolves.
4. **Context claims:** claim IDs, verified or not.
5. **What's worth taking from this source:** your assessment, separating ideas the evidence supports from ideas it doesn't.
6. **Needs new definitions:** what would have to be built to test the rest.

## 5. Tell the user
- what was saved;
- which rules were tested and how they did;
- the creator's running score, if any calls have resolved (`call score --creator "<name>"`);
- what can't be tested yet, and why.

One creator's track record needs many resolved forward calls before it means anything. Say so until it does.
