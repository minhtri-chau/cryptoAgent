"""Read stored protocol fundamentals for a coin, as known on a given day.

Values come only from daily snapshots (collectors/defillama.py), so "as of D" means the latest
snapshot taken on or before D: nothing is back-filled. Ratios are computed here, never in prompts:
- price_to_fees: market cap / (30-day fees x 365/30), price to annualised fees
- price_to_revenue: market cap / (30-day revenue x 365/30)
Market cap is DefiLlama's figure and is often missing; then the ratios are unavailable.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

METRICS = ("tvl", "mcap", "fees_24h", "fees_30d", "revenue_24h", "revenue_30d")
MIN_TVL = 1_000_000
ANNUALISE_30D = 365 / 30


def _ratio(mcap, flow_30d):
    if not mcap or not flow_30d:
        return None
    return round(mcap / (flow_30d * ANNUALISE_30D), 4)


def for_symbol(conn: sqlite3.Connection, symbol: str, as_of: str | None = None) -> list[dict]:
    """Every protocol family that reports this token, with its latest snapshot on or before as_of."""
    entities = conn.execute("SELECT id, key, name, category FROM fundamental_entities WHERE symbol = ? ORDER BY key",
                            (symbol.upper(),)).fetchall()
    out = []
    for e in entities:
        day = conn.execute("SELECT max(day) FROM fundamental_obs WHERE entity_id = ? AND (? IS NULL OR day <= ?)",
                           (e["id"], as_of, as_of)).fetchone()[0]
        if day is None:
            continue
        values = dict(conn.execute("SELECT metric, value FROM fundamental_obs WHERE entity_id = ? AND day = ?",
                                   (e["id"], day)).fetchall())
        if not ((values.get("tvl") or 0) >= MIN_TVL or values.get("fees_30d") or values.get("revenue_30d")):
            continue  # same rule as collection: dust or an inactive holder of the token is not its fundamentals
        first = conn.execute("SELECT min(day), count(DISTINCT day) FROM fundamental_obs WHERE entity_id = ?",
                             (e["id"],)).fetchone()
        row = {"family": e["key"], "name": e["name"], "category": e["category"], "snapshot_day": day,
               "tracked_since": first[0], "snapshots": first[1], **{m: values.get(m) for m in METRICS},
               "price_to_fees": _ratio(values.get("mcap"), values.get("fees_30d")),
               "price_to_revenue": _ratio(values.get("mcap"), values.get("revenue_30d"))}
        prior = conn.execute("SELECT value FROM fundamental_obs WHERE entity_id = ? AND metric = 'tvl'"
                             " AND day <= date(?, '-30 days') ORDER BY day DESC LIMIT 1", (e["id"], day)).fetchone()
        row["tvl_change_30d"] = round(values["tvl"] / prior[0] - 1, 6) if prior and prior[0] and values.get("tvl") \
            else None
        out.append(row)
    out.sort(key=lambda r: (r["tvl"] or 0), reverse=True)
    return out


SCAN_GRACE_DAYS = 1


def summary_by_symbol(conn: sqlite3.Connection, as_of: str | None = None) -> dict[str, dict]:
    """For scans: per token, the family with the largest TVL (all families stay visible via for_symbol).

    A scan as of day D (its last complete candle) may use a snapshot taken up to SCAN_GRACE_DAYS
    later: the daily task collects shortly after the UTC close, so its snapshot dated D+1 describes
    the end of D. Fundamentals are context here, never a backtest input.
    """
    cutoff = None if as_of is None else (date.fromisoformat(as_of) + timedelta(days=SCAN_GRACE_DAYS)).isoformat()
    symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM fundamental_entities")]
    out = {}
    for s in symbols:
        rows = for_symbol(conn, s, cutoff)
        if rows:
            top = rows[0]
            out[s] = {"family": top["family"], "families": len(rows), "snapshot_day": top["snapshot_day"],
                      "tvl": top["tvl"], "fees_30d": top["fees_30d"], "revenue_30d": top["revenue_30d"],
                      "price_to_fees": top["price_to_fees"], "tvl_change_30d": top["tvl_change_30d"]}
    return out
