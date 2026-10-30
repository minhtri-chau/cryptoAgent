"""Explicit, testable setups and the conditions that filter them.

A pattern is a boolean series: True at the close of day t when the setup is present,
computed from bars up to t only. Only patterns defined here can be backtested; a new idea
becomes a new function with tests, never a description in a prompt.

Conditions filter a pattern by any indicator or regime field of any market on the same
day, e.g. {"market": "bitstamp:BTC/USD", "field": "sma50_change20", "op": ">", "value": 0}.
"""

from __future__ import annotations

import operator
from collections.abc import Callable

import pandas as pd

from cryptoagent.quant import indicators as ind
from cryptoagent.quant import regime


def _breakout(n: int) -> Callable[[pd.DataFrame, pd.DataFrame], pd.Series]:
    def pattern(df, _):
        return df["close"] > df["high"].rolling(n).max().shift(1)
    pattern.__doc__ = f"Close above the highest high of the previous {n} bars."
    return pattern


def breakdown_20(df, _):
    """Close below the lowest low of the previous 20 bars."""
    return df["close"] < df["low"].rolling(20).min().shift(1)


def pullback_sma50(df, i):
    """Uptrend (close > SMA200, SMA50 > SMA200); low within 2% of SMA50 or below it; close back above SMA50."""
    return ((df["close"] > i["sma200"]) & (i["sma50"] > i["sma200"]) & (df["low"] <= i["sma50"] * 1.02)
            & (df["close"] > i["sma50"]))


def squeeze(df, i):
    """Bollinger bandwidth in the lowest 10% of the previous 365 bars."""
    width = (i["bb_upper"] - i["bb_lower"]) / i["bb_mid"]
    return width < width.rolling(365).quantile(0.10).shift(1)


def higher_highs_lows(df, _):
    """The last two confirmed pivot highs and the last two confirmed pivot lows are both rising."""
    events = ind.confirmed_pivots(df, 5)
    rising = {}
    for kind in ("high", "low"):
        e = events[events["kind"] == kind].drop_duplicates("confirmed", keep="last").set_index("confirmed")["price"]
        # State as of each day: was the latest confirmed pivot above the one before it?
        rising[kind] = (e > e.shift(1)).reindex(df.index).ffill().fillna(False).astype(bool)
    return rising["high"] & rising["low"]


def oversold_uptrend(df, i):
    """RSI 14 below 30 while the close is above SMA200."""
    return (i["rsi14"] < 30) & (df["close"] > i["sma200"])


def _ema_band(threshold: float) -> Callable[[pd.DataFrame, pd.DataFrame], pd.Series]:
    def pattern(df, i):
        return i["ema200_dev"] <= threshold
    pattern.__doc__ = f"Close at least {-threshold:.1%} below EMA200 (Crypto Asymmetry Engine band hypothesis)."
    return pattern


PATTERNS: dict[str, Callable[[pd.DataFrame, pd.DataFrame], pd.Series]] = {
    "breakout_20": _breakout(20),
    "breakout_55": _breakout(55),
    "breakdown_20": breakdown_20,
    "pullback_sma50": pullback_sma50,
    "squeeze": squeeze,
    "higher_highs_lows": higher_highs_lows,
    "oversold_uptrend": oversold_uptrend,
    "ema200_band_37": _ema_band(-0.375),
    "ema200_band_60": _ema_band(-0.60),
}

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq,
       "!=": operator.ne}


class SpecError(ValueError):
    pass


def evaluate(name: str, df: pd.DataFrame, indicators: pd.DataFrame | None = None) -> pd.Series:
    if name not in PATTERNS:
        raise SpecError(f"unknown pattern {name!r}; known: {', '.join(PATTERNS)}")
    i = ind.compute_all(df) if indicators is None else indicators
    # NaN comparisons are False, so a setup is never reported before its inputs exist.
    return PATTERNS[name](df, i).fillna(False).astype(bool)


def fields(df: pd.DataFrame) -> pd.DataFrame:
    """Every field a condition can refer to: indicators plus regime-v1 columns."""
    indicators = ind.compute_all(df)
    labels = regime.classify(df)
    return indicators.join(labels.drop(columns=[c for c in labels.columns if c in indicators.columns]))


def condition_mask(condition: dict, index: pd.DatetimeIndex, load: Callable[[str], pd.DataFrame],
                   own_market: str) -> pd.Series:
    """True on days where the condition holds. Days the other market has no data are False."""
    unknown = set(condition) - {"market", "field", "op", "value"}
    if unknown or not {"field", "op", "value"} <= set(condition):
        raise SpecError(f"condition needs field, op, value (and optional market): {condition}")
    if condition["op"] not in OPS:
        raise SpecError(f"unknown op {condition['op']!r}; use one of {', '.join(OPS)}")
    table = fields(load(condition.get("market", own_market)))
    if condition["field"] not in table.columns:
        raise SpecError(f"unknown field {condition['field']!r}; known: {', '.join(table.columns)}")
    column = table[condition["field"]].reindex(index)
    return OPS[condition["op"]](column, condition["value"]).fillna(False).astype(bool) & column.notna()


def active(df: pd.DataFrame) -> dict:
    """Which patterns are present on the last day of `df`, and how often each has fired before."""
    i = ind.compute_all(df)
    out = {}
    for name, fn in PATTERNS.items():
        signal = evaluate(name, df, i)
        out[name] = {"active": bool(signal.iloc[-1]) if len(signal) else False, "days_fired": int(signal.sum()),
                     "last_fired": signal[signal].index[-1].isoformat() if signal.any() else None,
                     "definition": (fn.__doc__ or "").strip()}
    return out
