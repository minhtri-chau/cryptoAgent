---
name: research
description: Research a cryptocurrency market topic or a specific coin on the web (tokenomics, unlocks, upgrades, regulation, exchange listings, protocol fundamentals, market structure) and save cited facts and labelled hypotheses to the project's knowledge base. Use before or during an investigation when context beyond price data matters.
---

# /research <topic, coin or question>

Goal: grow the project's persistent knowledge with **cited facts** and **clearly labelled hypotheses**, without repeating past work. Price, volume and backtest numbers come from the CLI, not from the web.

1. **Check what is already known.**
   - Run `.venv/Scripts/python -m cryptoagent note list --subject <COIN or MARKET>` and `note list --topic <topic>`. The topics are `market_structure, bitcoin, altcoins, fundamentals, tokenomics, regulation, macro, exchanges, strategy, events, data_sources`.
   - Run `note list --due` for claims whose review date has passed.
   - Don't re-research current claims unless they are due.
2. **Research** with WebSearch/WebFetch.
   - Prefer primary sources: the project's own docs and governance posts, token unlock schedules from the issuer, exchange announcements, regulator releases.
   - Use secondary sources (news, research reports) only when no primary one exists, and say so.
   - Never state a number you didn't read on a cited page.
   - Don't use prices, TVL or fees from the web. Use `cryptoagent` commands for those, and only for sources listed in `docs/data-sources.md`.
   - No price predictions, influencer opinions or social sentiment as facts. At most, they are hypotheses with low confidence.
3. **Save each finding as its own claim.**
   - A **fact**, stated directly by a source you read:
     ```
     .venv/Scripts/python -m cryptoagent note add --by agent --topic tokenomics --subject ARB --status fact \
       --text "..." --cite url:<url> --quote "<excerpt of 25 words or fewer>" [--review-after YYYY-MM-DD]
     ```
     Set `--review-after` for anything that changes: schedules, fees, policies, listings. It's usually 90 days out, or the day after a scheduled event.
   - A **hypothesis**, meaning your inference, a trend or an opinion:
     ```
     .venv/Scripts/python -m cryptoagent note add --by agent --topic altcoins --subject MARKET --status hypothesis \
       --confidence 0.4 --text "..." [--cite url:<url>] [--cite trial:<id>]
     ```
   - Evidence from this project is cited too: `--cite trial:21` or `--cite prediction:17`.
   - If a new fact replaces an old one, add it with `--supersedes <old id>`. Never try to edit or delete claims.
4. **Report back** briefly:
   - the claim IDs saved, split into facts and hypotheses;
   - anything that should change an open recommendation (`recommendations list --status open`);
   - open questions worth a later `/research`.

Keep facts narrow and checkable: one claim per statement, so each can be superseded on its own.
