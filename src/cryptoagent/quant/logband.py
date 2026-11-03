"""logband-v1: a long-term logarithmic regression band, fitted point-in-time.

Definition (changing any of it means a new version that the user approves):
- Fit, by ordinary least squares, log10(close) = a + b * log10(days since the anchor) on every
  daily close up to and including day t. The fit is redone for each day, so the band on day t
  uses only closes known at t. Charts fitted once over all history use future prices, which
  makes past touches of the band look perfect by construction.
- The anchor is the asset's genesis day where known (BTC 2009-01-03, ETH 2015-07-30),
  otherwise the first stored candle.
- Middle = the fitted line. Bands = middle +/- 1 and 2 standard deviations of that fit's own
  residuals (in log10 units), so `lb_z` is today's residual in those standard deviations.
- Nothing is reported before MIN_DAYS closes exist.
A ratio series (e.g. ETH/BTC) is fitted the same way. This is a description of where the price
sits against its own past trend; whether band touches predict anything is untested.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

VERSION = "logband-v1"
MIN_DAYS = 1095  # three years of closes before the first fit
GENESIS = {"BTC": "2009-01-03", "ETH": "2015-07-30"}


def anchor_for(market: str) -> str | None:
    """'coinbase:ETH/USD' or 'ETH/BTC' -> ETH's genesis day; None (first candle) if unknown."""
    base = market.partition(":")[2] or market
    return GENESIS.get(base.split("/")[0].upper())


def fit(close: pd.Series, anchor: str | None = None) -> pd.DataFrame:
    """Per-day point-in-time fit: lb_mid, lb_lower2, lb_lower1, lb_upper1, lb_upper2 (prices),
    lb_z, lb_slope (b) and lb_sigma (log10 units). Uses running sums, so day t sees only 0..t."""
    close = close[close > 0]
    start = pd.Timestamp(anchor) if anchor else close.index[0] - pd.Timedelta(days=1)
    days = (close.index - start).days.to_numpy(dtype=float)
    if len(days) and days.min() <= 0:
        raise ValueError(f"anchor {start.date()} must be before the first close {close.index[0].date()}")
    x, y = np.log10(days), np.log10(close.to_numpy(dtype=float))
    n = np.arange(1, len(x) + 1, dtype=float)
    sx, sy, sxx, sxy, syy = (np.cumsum(v) for v in (x, y, x * x, x * y, y * y))
    with np.errstate(divide="ignore", invalid="ignore"):
        b = (n * sxy - sx * sy) / (n * sxx - sx * sx)
        a = (sy - b * sx) / n
        sse = np.maximum(syy - a * sy - b * sxy, 0.0)
        sigma = np.sqrt(sse / (n - 2))
        mid = a + b * x
        out = pd.DataFrame(index=close.index)
        for name, k in (("lb_lower2", -2), ("lb_lower1", -1), ("lb_mid", 0), ("lb_upper1", 1), ("lb_upper2", 2)):
            out[name] = 10 ** (mid + k * sigma)
        out["lb_z"] = (y - mid) / sigma
    out["lb_slope"] = b
    out["lb_sigma"] = sigma
    out[n < MIN_DAYS] = np.nan
    return out


def ratio(numerator: pd.DataFrame, denominator: pd.DataFrame) -> pd.Series:
    """Close of one market divided by another's, on the days both have a candle."""
    both = numerator["close"].to_frame("n").join(denominator["close"].to_frame("d"), how="inner")
    return both["n"] / both["d"]


def report(close: pd.Series, anchor: str | None, last: int = 1) -> dict:
    bands = fit(close, anchor).dropna()
    rows = [{"day": ts.date().isoformat(), "close": float(close[ts]), **{k: float(v) for k, v in row.items()}}
            for ts, row in bands.tail(last).iterrows()]
    return {
        "version": VERSION, "anchor": anchor or f"first candle ({close.index[0].date()})",
        "first_close": close.index[0].date().isoformat(),
        "first_band_day": bands.index[0].date().isoformat() if len(bands) else None,
        "closes_in_fit": int(close.gt(0).sum()), "rows": rows,
        "notes": ["fitted only on closes up to each day (no look-ahead); today's band equals a fit on all stored data",
                  "bands are +/-1 and 2 standard deviations of the fit's own log residuals; lb_z is today's residual",
                  "descriptive, not a forecast: whether band touches predict returns is untested",
                  "rests on a few cycles; each new close moves the fit, so the band drifts over time"],
    }
