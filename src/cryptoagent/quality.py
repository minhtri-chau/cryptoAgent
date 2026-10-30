"""Data-quality checks over stored candles. Findings are recorded, never auto-corrected.

- missing_candle: a gap between two stored daily candles (one issue per gap, at its first
  missing day). Coinbase omits days with no trades, so a gap can be genuine inactivity.
- outlier_return: a close-to-close move between adjacent days larger than the threshold.
  Large moves do happen in crypto, so this flags data to look at, not data to drop.
- reference_mismatch: a USD close that differs from the independent reference price for
  the same asset and day by more than the tolerance.
invalid_ohlc issues are recorded by collectors at ingest, because those candles are never stored.
"""

from __future__ import annotations

import sqlite3

from cryptoagent import store

OUTLIER_RETURN = 0.5        # |close / previous close - 1| between adjacent days
REFERENCE_TOLERANCE = 0.03  # |close / reference - 1|


def _gaps(conn) -> list[tuple]:
    return conn.execute(
        """
        SELECT market_id, prev_ts, ts, CAST(round(julianday(ts) - julianday(prev_ts)) AS INTEGER) - 1 AS missing
        FROM (SELECT market_id, ts, lag(ts) OVER (PARTITION BY market_id ORDER BY ts) AS prev_ts
              FROM candles WHERE timeframe = '1d')
        WHERE prev_ts IS NOT NULL AND julianday(ts) - julianday(prev_ts) > 1.5
        """
    ).fetchall()


def _outliers(conn, threshold: float) -> list[tuple]:
    return conn.execute(
        """
        SELECT market_id, ts, prev_close, close
        FROM (SELECT market_id, ts, close,
                     lag(close) OVER (PARTITION BY market_id ORDER BY ts) AS prev_close,
                     lag(ts) OVER (PARTITION BY market_id ORDER BY ts) AS prev_ts
              FROM candles WHERE timeframe = '1d')
        WHERE prev_close IS NOT NULL AND julianday(ts) - julianday(prev_ts) < 1.5
          AND abs(close / prev_close - 1) > ?
        """,
        (threshold,),
    ).fetchall()


def _mismatches(conn, tolerance: float) -> list[tuple]:
    return conn.execute(
        """
        SELECT c.market_id, c.ts, c.close, r.value, s.name, r.metric
        FROM candles c
        JOIN markets m ON m.id = c.market_id AND m.quote = 'USD'
        JOIN reference_prices r ON r.asset_id = m.base_asset_id AND r.day = substr(c.ts, 1, 10)
        JOIN sources s ON s.id = r.source_id
        WHERE c.timeframe = '1d' AND abs(c.close / r.value - 1) > ?
        """,
        (tolerance,),
    ).fetchall()


def run_checks(conn: sqlite3.Connection, outlier_return: float = OUTLIER_RETURN,
               reference_tolerance: float = REFERENCE_TOLERANCE) -> dict:
    """Run every check over all stored candles; returns how many new issues each found."""
    new = {"missing_candle": 0, "outlier_return": 0, "reference_mismatch": 0}
    with conn:
        for market_id, prev_ts, ts, missing in _gaps(conn):
            first_missing = store.iso_from_ms(store.ms_from_iso(prev_ts) + store.DAY_MS)
            new["missing_candle"] += store.record_issue(
                conn, "missing_candle", market_id, first_missing, {"missing_days": missing, "next_candle": ts})
        for market_id, ts, prev_close, close in _outliers(conn, outlier_return):
            new["outlier_return"] += store.record_issue(
                conn, "outlier_return", market_id, ts,
                {"prev_close": prev_close, "close": close, "return": round(close / prev_close - 1, 6),
                 "threshold": outlier_return})
        for market_id, ts, close, ref, source, metric in _mismatches(conn, reference_tolerance):
            new["reference_mismatch"] += store.record_issue(
                conn, "reference_mismatch", market_id, ts,
                {"close": close, "reference": ref, "reference_source": f"{source}:{metric}",
                 "diff": round(close / ref - 1, 6), "tolerance": reference_tolerance})
    return new


def summary(conn: sqlite3.Connection, market: str | None = None, limit: int = 20) -> dict:
    """Issue counts by market and check, plus the most recent issues."""
    where, params = ("WHERE (s.name || ':' || m.symbol) = ?", [market]) if market else ("", [])
    counts = conn.execute(
        f"SELECT s.name || ':' || m.symbol AS market, q.check_name, count(*) AS issues, min(q.ts) AS first_ts,"
        f" max(q.ts) AS last_ts FROM quality_issues q JOIN markets m ON m.id = q.market_id"
        f" JOIN sources s ON s.id = m.source_id {where} GROUP BY 1, 2 ORDER BY 1, 2",
        params,
    ).fetchall()
    recent = conn.execute(
        f"SELECT s.name || ':' || m.symbol AS market, q.check_name, q.ts, q.detail FROM quality_issues q"
        f" JOIN markets m ON m.id = q.market_id JOIN sources s ON s.id = m.source_id {where}"
        f" ORDER BY q.ts DESC, q.id DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    totals: dict[str, int] = {}
    for row in counts:
        totals[row["check_name"]] = totals.get(row["check_name"], 0) + row["issues"]
    return {"totals": totals, "by_market": [dict(r) for r in counts], "recent": [dict(r) for r in recent]}
