"""Point-in-time tradable universe and the scanner built on it.

universe-v1, as of day D, using only candles on or before D:
- a USD market of the source with a daily candle within MAX_STALENESS_DAYS of D
  (a delisted market stops qualifying after its last candle, and qualifies before it)
- at least MIN_HISTORY_DAYS daily candles
- median daily dollar volume (close x volume) over the last LIQUIDITY_WINDOW candles of at
  least MIN_MEDIAN_DOLLAR_VOLUME

universe-v2 (approved by the user 2026-10-30; the default) is v1 plus two exclusions:
- assets that track something else, listed in EXCLUDED_ASSETS: fiat stablecoins, a
  commodity token, and wrapped or staked versions of other coins (duplicate exposure)
- a backstop for pegged assets not yet listed: 30-day annualised volatility of daily log
  returns below MIN_VOL30, measured on candles up to D (no real crypto asset in the 2026
  universe was below 29%; stablecoins sit around 0.3–2%)
v1 stays callable so earlier scans can be reproduced.

The thresholds and lists are the definition: changing one makes a new version that the
user approves.

The scanner reports facts per market and sorts by a key the caller chooses. Deciding which
markets are interesting is reasoning, left to the altcoin-scanner subagent.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from cryptoagent.quant import indicators as ind
from cryptoagent.quant import patterns, regime

VERSIONS = ("universe-v1", "universe-v2")
VERSION = "universe-v2"  # default
MAX_STALENESS_DAYS = 3
MIN_HISTORY_DAYS = 200
LIQUIDITY_WINDOW = 30
MIN_MEDIAN_DOLLAR_VOLUME = 1_000_000.0
BENCHMARK = "BTC/USD"
MIN_VOL30 = 0.10  # universe-v2 backstop
EXCLUDED_ASSETS = {  # universe-v2; base symbols as Coinbase lists them
    "fiat_stablecoin": ("USDT", "DAI", "PYUSD", "EURC", "GUSD", "PAX", "BUSD", "USD1", "USDS"),
    "commodity_token": ("PAXG",),
    "wrapped_or_staked": ("WBTC", "CBETH", "LSETH", "MSOL", "JITOSOL"),
}
_EXCLUDED = {symbol: category for category, symbols in EXCLUDED_ASSETS.items() for symbol in symbols}


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


def membership(df: pd.DataFrame, as_of: pd.Timestamp, base: str | None = None,
               version: str = VERSION) -> tuple[bool, str, dict]:
    """Whether one market qualifies on as_of, why not if it doesn't, and the measured values."""
    facts = {"history_days": len(df), "last_candle": df.index[-1].date().isoformat() if len(df) else None}
    if version not in VERSIONS:
        raise ValueError(f"unknown universe version {version!r}; use {', '.join(VERSIONS)}")
    if version == "universe-v2" and base in _EXCLUDED:
        facts["excluded_category"] = _EXCLUDED[base]
        return False, "pegged_or_wrapped", facts
    if df.empty or (as_of - df.index[-1]).days > MAX_STALENESS_DAYS:
        return False, "stale", facts
    if len(df) < MIN_HISTORY_DAYS:
        return False, "short_history", facts
    recent = df.iloc[-LIQUIDITY_WINDOW:]
    facts["median_dollar_volume"] = round(float((recent["close"] * recent["volume"]).median()), 2)
    if facts["median_dollar_volume"] < MIN_MEDIAN_DOLLAR_VOLUME:
        return False, "illiquid", facts
    if version == "universe-v2":
        vol = ind.realized_vol(df["close"].iloc[-(LIQUIDITY_WINDOW + 1):], LIQUIDITY_WINDOW).iloc[-1]
        if vol == vol and vol < MIN_VOL30:
            facts["vol30"] = round(float(vol), 6)
            return False, "low_volatility", facts
    return True, "member", facts


def _round(value, digits=6):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return round(float(value), digits)


def scan(conn: sqlite3.Connection, as_of: str | None = None, source: str = "coinbase", quote: str = "USD",
         sort: str = "rs90", limit: int | None = None, trials_by_market: dict | None = None,
         version: str = VERSION) -> dict:
    if as_of is None:
        as_of = conn.execute(
            "SELECT substr(max(c.ts), 1, 10) FROM candles c JOIN markets m ON m.id = c.market_id"
            " JOIN sources s ON s.id = m.source_id WHERE s.name = ? AND m.symbol = ? AND c.timeframe = '1d'",
            (source, BENCHMARK)).fetchone()[0]
    day = pd.Timestamp(as_of)
    data = _candles(conn, source, quote, as_of)
    counts = {"markets_with_history": len(data), "stale": 0, "short_history": 0, "illiquid": 0}
    if version == "universe-v2":
        counts.update(pegged_or_wrapped=0, low_volatility=0)
    counts["universe"] = 0
    excluded = []
    bench = data.get(BENCHMARK)
    bench_roc = {}
    if bench is not None and len(bench):
        b = ind.compute_all(bench).iloc[-1]
        bench_roc = {n: b[f"roc{n}"] for n in (30, 90)}
    rows = []
    for symbol, df in data.items():
        ok, reason, facts = membership(df, day, symbol.split("/")[0], version)
        if not ok:
            counts[reason] += 1
            if reason in ("pegged_or_wrapped", "low_volatility"):
                excluded.append({"market": f"{source}:{symbol}", "reason": reason,
                                 **{k: facts[k] for k in ("excluded_category", "vol30") if k in facts}})
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
    rules = {"max_staleness_days": MAX_STALENESS_DAYS, "min_history_days": MIN_HISTORY_DAYS,
             "liquidity_window": LIQUIDITY_WINDOW, "min_median_dollar_volume": MIN_MEDIAN_DOLLAR_VOLUME}
    out = {"version": version, "as_of": as_of, "benchmark": f"{source}:{BENCHMARK}", "rules": rules}
    if version == "universe-v2":
        rules.update(min_vol30=MIN_VOL30, excluded_assets=EXCLUDED_ASSETS)
        out["excluded"] = excluded
    return {**out, "counts": counts, "sorted_by": f"{sort} (descending)", "markets": rows[:limit] if limit else rows}
