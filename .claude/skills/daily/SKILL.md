---
name: daily
description: The daily session with the crypto research agent. Reviews what changed since the last briefing (resolved predictions and recommendations, data problems, strategy stages), the BTC backdrop and the scan, checks open calls against their invalidation conditions, decides whether anything deserves a full investigation today, records a journal briefing, and tells the user what needs their decision. Use when the user says good morning, asks for the daily update, or types /daily.
---

# /daily [unattended]

You are the project's lead researcher, starting the day. Work from the record, not from memory. Every number comes from `.venv/Scripts/python -m cryptoagent ...`.

`unattended` means a scheduled run with no user present:
- Do at most one investigation.
- Ask no questions.
- Never run any user-only command (`strategy authorize-test|approve|reject|retire`, or anything with `--by user`).

## 1. What changed since last time
- Run `digest`. It starts from the last journal briefing.
- If `data.latest_daily_candle` is older than yesterday (UTC), or `failed_runs_since` isn't empty, say so first. Then run the missing collectors (`collect coinbase`, `collect bitstamp`, `collect coinmetrics`, `collect defillama`), followed by `quality`, `evaluate`, `predict baselines`, `strategy paper` and `report`.
- If today's file in `reports/` doesn't exist, run `report`, then read it.

## 2. Score what resolved
For each resolved **judgment** prediction and each resolved recommendation in `digest.resolved_since`:
- Say plainly whether it was right, with numbers:
  - for a prediction: P(up) against the outcome, its Brier score, and the base-rate model's Brier on the same date (`predictions list --market M`);
  - for a recommendation: the market return over its horizon and, for a buy, the replayed trade return.
- If a call was wrong in a way that teaches something, record the lesson as a **hypothesis**:
  ```
  note add --by agent --topic strategy --subject COIN --status hypothesis --confidence 0.3 --text "..." --cite prediction:ID
  ```
  One outcome is anecdote. Don't change any rule because of it.
- Report the running record from `digest.track_record`, with its sample-size warnings.

## 3. Today's market
From the report, cover in a few lines:
- the BTC regime;
- the analogues, with the caveat that they lost to the base rate in trials;
- the scanner breadth (how many coins show each setup);
- the strategies section.

Use `bitcoin-analyst` only if something material changed: the regime label, a new active pattern on BTC, a band reached, or a large move.

## 4. Open calls
For each open recommendation (`recommendations list --status open`):
- Check its stated invalidation condition against today's `indicators` and `patterns` output.
- If it's invalidated, record a new recommendation for that market reflecting the change, and say so. The old one stays on record and is still scored.

## 5. Decide whether to investigate
Investigate at most one market by default (two only if the user asks), and only if one of these holds:
- an approved strategy produced a signal (`strategy signals`);
- a defined setup is active on a liquid coin that has a **validated** history: a pooled or single-market trial that passed validation;
- something material appeared in the data or in `note list --due`.

Otherwise the answer is "nothing worth investigating today", which is the normal case. Never start an investigation just to have something to do: every trial counts towards multiple testing.

If you do investigate, run the `/investigate` workflow. It records its own prediction and recommendation.

## 6. Record the briefing
```
journal add --kind briefing --by agent --text "<4-8 sentences: data status, what resolved and how it scored, market state, open calls, today's decision>" \
  --report reports/YYYY-MM-DD.md --evidence "{\"predictions\": [..], \"recommendations\": [..], \"trials\": [..], \"claims\": [..]}"
```

## 7. Tell the user
Keep it short. Paste nothing long, and keep it to the order below:
1. A one-line market state.
2. What resolved, and whether we were right.
3. Today's action: usually "no trade; nothing qualifies".
4. **Decisions waiting for the user.** List each one:
   - strategies at `validation_passed` (the sealed test needs authorisation) or `paper_review` (approve or reject) from `strategy list`;
   - the open terms review, if the `v0.x` tags still don't exist (`git tag`);
   - anything the risk reviewer flagged.
5. One question at most, if something needs the user's view.

End with: "Research output, not financial advice."
