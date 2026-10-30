"""Market regime labels, version regime-v1. Descriptive, not trade rules.

The thresholds below are the definition of regime-v1: changing any of them makes a new
version (and needs the user's approval, like any strategy change). Every field at day t
uses only bars up to t.

- trend: 'uptrend' when close > SMA200 and SMA200 is higher than 20 days ago;
  'downtrend' when close < SMA200 and SMA200 is lower; otherwise 'transition'.
- volatility: percentile of 30-day realised volatility among all earlier days
  (needs MIN_VOL_HISTORY of them): 'low' below 1/3, 'high' above 2/3, else 'normal'.
- drawdown from the all-time high close, deviation from EMA200, and the deepest of the
  EMA200 deviation bands reached (from the earlier Crypto Asymmetry Engine; a hypothesis
  to test, not a signal).
- halving cycle: number of halvings so far (0 before the first) and days since the last one.
"""

from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

from cryptoagent.quant import indicators as ind

VERSION = "regime-v1"
TREND_MA = 200
SLOPE_DAYS = 20
VOL_DAYS = 30
MIN_VOL_HISTORY = 365
VOL_LOW, VOL_HIGH = 1 / 3, 2 / 3
EMA200_BANDS = (-0.375, -0.60, -0.75)

# Block timestamps (UTC) of heights 210,000 / 420,000 / 630,000 / 840,000, checked on
# mempool.space 2026-10-30. The day of each halving counts as day 0 of the new cycle.
HALVINGS = tuple(pd.Timestamp(d) for d in ("2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20"))


def _trend(close: pd.Series, ma: pd.Series) -> pd.Series:
    slope = ma - ma.shift(SLOPE_DAYS)
    out = pd.Series("transition", index=close.index, dtype=object)
    out[(close > ma) & (slope > 0)] = "uptrend"
    out[(close < ma) & (slope < 0)] = "downtrend"
    return out.where(slope.notna())


def _vol_percentile(vol: pd.Series) -> pd.Series:
    """Share of earlier valid days with lower volatility than today (strictly earlier days only)."""
    out = np.full(len(vol), np.nan)
    seen: list[float] = []  # kept sorted
    for i, v in enumerate(vol.to_numpy()):
        if not np.isnan(v):
            if len(seen) >= MIN_VOL_HISTORY:
                out[i] = bisect.bisect_left(seen, v) / len(seen)
            bisect.insort(seen, v)
    return pd.Series(out, index=vol.index)


def _band(dev: pd.Series) -> pd.Series:
    out = pd.Series(None, index=dev.index, dtype=object)
    for threshold in EMA200_BANDS:  # shallow to deep, so the deepest band reached wins
        out[dev <= threshold] = f"{threshold:.1%}"
    return out


def classify(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]
    ma = ind.sma(close, TREND_MA)
    ema = ind.ema(close, TREND_MA)
    vol = ind.realized_vol(close, VOL_DAYS)
    pct = _vol_percentile(vol)
    out = pd.DataFrame(index=df.index)
    out["close"] = close
    out["sma200"] = ma
    out["sma200_change20"] = ma / ma.shift(SLOPE_DAYS) - 1
    out["trend"] = _trend(close, ma)
    out["vol30"] = vol
    out["vol30_percentile"] = pct
    vol_label = pd.Series("normal", index=df.index, dtype=object)
    vol_label[pct < VOL_LOW] = "low"
    vol_label[pct > VOL_HIGH] = "high"
    out["volatility"] = vol_label.where(pct.notna())
    out["drawdown_ath"] = ind.drawdown_from_ath(close)
    out["ema200_dev"] = ind.deviation(close, ema)
    out["ema200_band"] = _band(out["ema200_dev"])
    cycle = np.searchsorted(pd.DatetimeIndex(HALVINGS), df.index, side="right")  # halvings on or before each day
    out["halving_cycle"] = cycle
    since = (df.index - pd.DatetimeIndex(HALVINGS)[np.maximum(cycle - 1, 0)]).days
    out["days_since_halving"] = pd.Series(since, index=df.index, dtype=float).where(cycle > 0)
    return out


def snapshot(df: pd.DataFrame) -> dict:
    """The regime on the last day of `df`, as JSON-ready values."""
    if df.empty:
        return {"version": VERSION, "as_of": None}
    row = classify(df).iloc[-1]
    out = {"version": VERSION, "as_of": df.index[-1].date().isoformat()}
    for key, value in row.items():
        if isinstance(value, float):
            out[key] = None if value != value else round(value, 6)
        else:
            out[key] = value
    out["halving_cycle"] = int(row["halving_cycle"])
    if out["days_since_halving"] is not None:
        out["days_since_halving"] = int(out["days_since_halving"])
    return out
