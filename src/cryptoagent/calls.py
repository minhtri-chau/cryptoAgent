"""Outside commentators' dated calls, scored like the agent's own.

A call says a market will be up / down at a deadline, or reach a target above / below by a
deadline. It is anchored to the stored close on the publication day. Each call also gets a naive
base rate: how often the same outcome happened over windows of the same length in the market's
history before publication. A creator adds value only if their hit rate beats those base rates,
which is what `score` reports.

'forward' calls were recorded within FORWARD_DAYS of publication. 'historical' ones were found
later; they are reported separately because channels followed today were picked after their past
calls were known (survivorship), so they flatter the creator.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import numpy as np

from cryptoagent.predictions import GRACE_DAYS, SMALL_SAMPLE, LedgerError
from cryptoagent.quant.series import load_daily, market_id

CALL_TYPES = ("up", "down", "reach_above", "reach_below")
FORWARD_DAYS = 7
MAX_QUOTE_WORDS = 25
MIN_WINDOWS = 30


def base_rate(df, call_type: str, horizon: int, target_move: float | None = None) -> float | None:
    """Share of past `horizon`-day windows (all inside `df`) in which the call's outcome happened."""
    close, high, low = (df[c].to_numpy(dtype=float) for c in ("close", "high", "low"))
    n = len(df) - horizon
    if n < MIN_WINDOWS:
        return None
    if call_type in ("up", "down"):
        up = close[horizon:] > close[:n]
        return float(up.mean() if call_type == "up" else (~up).mean())
    hits = []
    for s in range(n):
        window = slice(s + 1, s + horizon + 1)
        if call_type == "reach_above":
            hits.append(high[window].max() / close[s] - 1 >= target_move)
        else:
            hits.append(low[window].min() / close[s] - 1 <= target_move)
    return float(np.mean(hits))


def record(conn: sqlite3.Connection, *, creator: str, source_url: str, published_on: str, market: str,
           call_type: str, deadline: str, recorded_by: str, target: float | None = None,
           quote: str | None = None, today: date | None = None) -> dict:
    errors = []
    if call_type not in CALL_TYPES:
        errors.append(f"call type must be one of {', '.join(CALL_TYPES)}")
    if (call_type in ("reach_above", "reach_below")) != (target is not None):
        errors.append("reach_above / reach_below need --target; up / down take none")
    if quote and len(quote.split()) > MAX_QUOTE_WORDS:
        errors.append(f"quote is longer than {MAX_QUOTE_WORDS} words: keep only the call itself")
    try:
        published, due = date.fromisoformat(published_on), date.fromisoformat(deadline)
        if due <= published:
            errors.append("the deadline must be after the publication day")
    except ValueError:
        errors.append("dates must be YYYY-MM-DD")
    if errors:
        raise LedgerError("; ".join(errors))
    df = load_daily(conn, market, published_on)
    if df.empty or df.index[-1].date() != published:
        raise LedgerError(f"no stored {market} candle on {published_on}: a call is anchored to that day's close")
    close = float(df["close"].iloc[-1])
    if call_type == "reach_above" and target <= close:
        raise LedgerError(f"target {target} is not above the publication-day close {close}")
    if call_type == "reach_below" and target >= close:
        raise LedgerError(f"target {target} is not below the publication-day close {close}")
    today = today or date.today()
    timing = "forward" if (today - published).days <= FORWARD_DAYS else "historical"
    with conn:
        call_id = conn.execute(
            "INSERT INTO external_calls (recorded_by, creator, source_url, quote, published_on, market_id,"
            " published_close, call_type, target, deadline, timing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (recorded_by, creator.strip(), source_url, quote, published_on, market_id(conn, market), close, call_type,
             target, deadline, timing)).lastrowid
    return {"id": call_id, "creator": creator.strip(), "market": market, "published_on": published_on,
            "published_close": close, "call_type": call_type, "target": target, "deadline": deadline,
            "timing": timing}


def resolve(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """Resolve calls whose deadline has passed, and target calls as soon as the target is touched."""
    rows = conn.execute(
        "SELECT c.*, s.name || ':' || m.symbol AS market FROM external_calls c JOIN markets m ON m.id = c.market_id"
        " JOIN sources s ON s.id = m.source_id LEFT JOIN external_outcomes o ON o.call_id = c.id"
        " WHERE o.call_id IS NULL").fetchall()
    counts = {"resolved": 0, "unresolvable": 0, "waiting": 0}
    for c in rows:
        df = load_daily(conn, c["market"])
        after = df[(df.index > np.datetime64(c["published_on"])) & (df.index <= np.datetime64(c["deadline"]))]
        horizon = (date.fromisoformat(c["deadline"]) - date.fromisoformat(c["published_on"])).days
        history = df[df.index <= np.datetime64(c["published_on"])]
        move = c["target"] / c["published_close"] - 1 if c["target"] else None
        latest = df.index[-1].date() if len(df) else None
        hit = extreme = None
        if c["call_type"] == "reach_above" and len(after):
            extreme = float(after["high"].max())
            hit = 1 if extreme >= c["target"] else None
        elif c["call_type"] == "reach_below" and len(after):
            extreme = float(after["low"].min())
            hit = 1 if extreme <= c["target"] else None
        deadline_passed = latest is not None and latest >= date.fromisoformat(c["deadline"])
        if hit is None and deadline_passed and len(after):
            last_close = float(after["close"].iloc[-1])
            hit = {"up": int(last_close > c["published_close"]), "down": int(last_close < c["published_close"]),
                   "reach_above": 0, "reach_below": 0}[c["call_type"]]
        if hit is not None:
            with conn:
                conn.execute(
                    "INSERT INTO external_outcomes (call_id, status, hit, deadline_close, extreme, base_rate)"
                    " VALUES (?, 'resolved', ?, ?, ?, ?)",
                    (c["id"], hit, float(after["close"].iloc[-1]) if len(after) else None, extreme,
                     base_rate(history, c["call_type"], horizon, move)))
            counts["resolved"] += 1
        elif (today or date.today()) > date.fromisoformat(c["deadline"]) + timedelta(days=GRACE_DAYS):
            # the deadline is long past but the market has no candles up to it (e.g. it was delisted)
            with conn:
                conn.execute("INSERT INTO external_outcomes (call_id, status, note) VALUES (?, 'unresolvable', ?)",
                             (c["id"], "no candles between publication and deadline"))
            counts["unresolvable"] += 1
        else:
            counts["waiting"] += 1
    return counts


def score(conn: sqlite3.Connection, creator: str | None = None) -> list[dict]:
    rows = conn.execute(
        "SELECT c.creator, c.timing, c.call_type, o.status, o.hit, o.base_rate FROM external_calls c"
        " LEFT JOIN external_outcomes o ON o.call_id = c.id WHERE (? IS NULL OR c.creator = ?)",
        (creator, creator)).fetchall()
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["creator"], r["timing"]), []).append(r)
    out = []
    for (name, timing), items in sorted(groups.items()):
        done = [r for r in items if r["status"] == "resolved"]
        entry = {"creator": name, "timing": timing, "calls": len(items), "open": sum(r["status"] is None for r in items),
                 "resolved": len(done)}
        if done:
            hit_rate = float(np.mean([r["hit"] for r in done]))
            bases = [r["base_rate"] for r in done if r["base_rate"] is not None]
            entry.update({"hit_rate": round(hit_rate, 4),
                          "mean_base_rate": round(float(np.mean(bases)), 4) if bases else None,
                          "edge_vs_base_rate": round(hit_rate - float(np.mean(bases)), 4) if bases else None})
            if len(done) < SMALL_SAMPLE:
                entry["warning"] = f"only {len(done)} resolved calls: too few to judge (need {SMALL_SAMPLE}+)"
            if timing == "historical":
                entry["caution"] = "historical calls flatter the creator: channels are chosen after their past calls"
        out.append(entry)
    return out


def list_calls(conn: sqlite3.Connection, creator: str | None = None, limit: int = 50) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT c.id, c.creator, c.timing, s.name || ':' || m.symbol AS market, c.call_type, c.target,"
        " c.published_on, c.published_close, c.deadline, c.source_url, c.quote, coalesce(o.status, 'open') AS status,"
        " o.hit, o.base_rate FROM external_calls c JOIN markets m ON m.id = c.market_id"
        " JOIN sources s ON s.id = m.source_id LEFT JOIN external_outcomes o ON o.call_id = c.id"
        " WHERE (? IS NULL OR c.creator = ?) ORDER BY c.id DESC LIMIT ?", (creator, creator, limit))]
