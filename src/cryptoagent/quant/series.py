"""Load stored candles as a pandas frame, as they were known at a point in time."""

from __future__ import annotations

import sqlite3

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


class UnknownMarket(ValueError):
    pass


def market_id(conn: sqlite3.Connection, spec: str) -> int:
    """Resolve 'SOURCE:SYMBOL' (e.g. 'coinbase:BTC/USD') to a market id."""
    source, _, symbol = spec.partition(":")
    row = conn.execute(
        "SELECT m.id FROM markets m JOIN sources s ON s.id = m.source_id WHERE s.name = ? AND m.symbol = ?",
        (source, symbol),
    ).fetchone()
    if row is None:
        raise UnknownMarket(f"unknown market {spec!r}; expected SOURCE:SYMBOL, see `markets`")
    return row[0]


def load_daily(conn: sqlite3.Connection, spec: str, as_of: str | None = None) -> pd.DataFrame:
    """Daily candles indexed by UTC day (Timestamp), oldest first.

    `as_of` ('YYYY-MM-DD') keeps only candles for days on or before it: the state of the
    market as it could have been known at the end of that day.
    """
    rows = conn.execute(
        "SELECT substr(ts, 1, 10) AS day, open, high, low, close, volume FROM candles"
        " WHERE market_id = ? AND timeframe = '1d' AND (? IS NULL OR substr(ts, 1, 10) <= ?) ORDER BY ts",
        (market_id(conn, spec), as_of, as_of),
    ).fetchall()
    frame = pd.DataFrame([tuple(r) for r in rows], columns=["day", *COLUMNS])
    frame.index = pd.to_datetime(frame.pop("day"))
    return frame.astype(float)


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """ISO weeks (Monday start) built from daily candles, indexed by the week's Monday.

    A week is included only once its Sunday is in `daily`, so the last bar is never a week
    still in progress. `days` counts the daily candles in the week (7 unless the exchange
    had no trades on some day, or the market listed mid-week).
    """
    if daily.empty:
        return daily.assign(days=pd.Series(dtype=int))
    weekly = daily.resample("W-MON", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    weekly["days"] = daily["close"].resample("W-MON", label="left", closed="left").count()
    weekly = weekly[weekly["days"] > 0]
    return weekly[weekly.index + pd.Timedelta(days=6) <= daily.index[-1]]


def load(conn: sqlite3.Connection, spec: str, timeframe: str = "1d", as_of: str | None = None) -> pd.DataFrame:
    daily = load_daily(conn, spec, as_of)
    if timeframe == "1d":
        return daily
    if timeframe == "1w":
        return to_weekly(daily).drop(columns="days")
    raise ValueError(f"unsupported timeframe {timeframe!r}; use 1d or 1w")
