"""Point-in-time tradable universe (universe-v1) and the scanner built on it.

universe-v1, as of day D, using only candles on or before D:
- a USD market of the source with a daily candle within MAX_STALENESS_DAYS of D
  (a delisted market stops qualifying after its last candle, and qualifies before it)
- at least MIN_HISTORY_DAYS daily candles
- median daily dollar volume (close x volume) over the last LIQUIDITY_WINDOW candles of at
  least MIN_MEDIAN_DOLLAR_VOLUME
The thresholds are the definition: changing one makes a new version that the user approves.

The scanner reports facts per market and sorts by a key the caller chooses. Deciding which
markets are interesting is reasoning, left to the altcoin-scanner subagent.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from cryptoagent.quant import indicators as ind
from cryptoagent.quant import patterns, regime

VERSION = "universe-v1"
MAX_STALENESS_DAYS = 3
MIN_HISTORY_DAYS = 200
LIQUIDITY_WINDOW = 30
MIN_MEDIAN_DOLLAR_VOLUME = 1_000_000.0
BENCHMARK = "BTC/USD"


def _candles(conn: sqlite3.Connection, source: str, quote: str, as_of: str) -> dict[str, pd.DataFrame]:
    rows = conn.execute(
        "SELECT m.symbol, substr(c.ts, 1, 10) AS day, c.open, c.high, c.low, c.close, c.volume"
        " FROM candles c JOIN markets m ON m.id = c.market_id JOIN sources s ON s.id = m.source_id"
        " WHERE s.name = ? AND m.quote = ? AND c.timeframe = '1d' AND substr(c.ts, 1, 10) <= ?"
        " ORDER BY m.symbol, c.ts",
        (source, quote, as_of),
    ).fetchall()
    frame = pd.DataFrame([tuple(r) for r in rows], columns=["symbol", "day", "open", "high", "low", "close", "volume"])
    frame["day"] = pd.to_datetime(frame["day"])
    return {s: g.drop(columns="symbol").set_index("day").astype(float) for s, g in frame.groupby("symbol")}


def membership(df: pd.DataFrame, as_of: pd.Timestamp) -> tuple[bool, str, dict]:
    """Whether one market qualifies on as_of, why not if it doesn't, and the measured values."""
    facts = {"history_days": len(df), "last_candle": df.index[-1].date().isoformat() if len(df) else None}
    if df.empty or (as_of - df.index[-1]).days > MAX_STALENESS_DAYS:
        return False, "stale", facts
    if len(df) < MIN_HISTORY_DAYS:
        return False, "short_history", facts
    recent = df.iloc[-LIQUIDITY_WINDOW:]
    facts["median_dollar_volume"] = round(float((recent["close"] * recent["volume"]).median()), 2)
    if facts["median_dollar_volume"] < MIN_MEDIAN_DOLLAR_VOLUME:
        return False, "illiquid", facts
    return True, "member", facts


def _round(value, digits=6):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return round(float(value), digits)


def scan(conn: sqlite3.Connection, as_of: str | None = None, source: str = "coinbase", quote: str = "USD",
         sort: str = "rs90", limit: int | None = None, trials_by_market: dict | None = None) -> dict:
    if as_of is None:
        as_of = conn.execute(
            "SELECT substr(max(c.ts), 1, 10) FROM candles c JOIN markets m ON m.id = c.market_id"
            " JOIN sources s ON s.id = m.source_id WHERE s.name = ? AND m.symbol = ? AND c.timeframe = '1d'",
            (source, BENCHMARK)).fetchone()[0]
    day = pd.Timestamp(as_of)
    data = _candles(conn, source, quote, as_of)
    counts = {"markets_with_history": len(data), "stale": 0, "short_history": 0, "illiquid": 0, "universe": 0}
    bench = data.get(BENCHMARK)
    bench_roc = {}
    if bench is not None and len(bench):
        b = ind.compute_all(bench).iloc[-1]
        bench_roc = {n: b[f"roc{n}"] for n in (30, 90)}
    rows = []
    for symbol, df in data.items():
        ok, reason, facts = membership(df, day)
        if not ok:
            counts[reason] += 1
            continue
        counts["universe"] += 1
        i = ind.compute_all(df)
        last = i.iloc[-1]
        label = regime.classify(df).iloc[-1]
        active = [name for name in patterns.PATTERNS if patterns.evaluate(name, df, i).iloc[-1]]
        row = {"market": f"{source}:{symbol}", **facts, "close": last["close"],
               "trend": label["trend"] if isinstance(label["trend"], str) else None,
               "volatility": label["volatility"] if isinstance(label["volatility"], str) else None,
               "vol30": _round(last["vol30"]), "rsi14": _round(last["rsi14"], 2),
               "atr14_pct": _round(last["atr14_pct"]), "sma200_dev": _round(last["close"] / last["sma200"] - 1),
               "drawdown_ath": _round(last["drawdown_ath"]),
               "roc30": _round(last["roc30"]), "roc90": _round(last["roc90"]), "active_patterns": active}
        for n in (30, 90):
            asset, btc = last[f"roc{n}"], bench_roc.get(n)
            row[f"rs{n}"] = _round((1 + asset) / (1 + btc) - 1) if btc is not None and not np.isnan(asset) else None
        if trials_by_market is not None:
            row["trials"] = trials_by_market.get(row["market"], 0)
        rows.append(row)
    if rows and sort not in rows[0]:
        raise patterns.SpecError(f"unknown sort key {sort!r}; use one of {', '.join(k for k in rows[0])}")
    rows.sort(key=lambda r: (r[sort] is None, -(r[sort] or 0) if isinstance(r[sort], (int, float)) else 0))
    return {"version": VERSION, "as_of": as_of, "benchmark": f"{source}:{BENCHMARK}",
            "rules": {"max_staleness_days": MAX_STALENESS_DAYS, "min_history_days": MIN_HISTORY_DAYS,
                      "liquidity_window": LIQUIDITY_WINDOW, "min_median_dollar_volume": MIN_MEDIAN_DOLLAR_VOLUME},
            "counts": counts, "sorted_by": f"{sort} (descending)",
            "markets": rows[:limit] if limit else rows}
