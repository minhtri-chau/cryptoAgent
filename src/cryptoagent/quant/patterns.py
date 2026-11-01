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


def _daily_only(fn: Callable[[pd.DataFrame, pd.DataFrame], pd.Series]) -> Callable[[pd.DataFrame, pd.DataFrame], pd.Series]:
    """Weekly-aware patterns are defined on daily bars; on other timeframes they never fire."""
    def pattern(df, i):
        return fn(df, i) if ind.is_daily(df) else pd.Series(False, index=df.index)
    pattern.__doc__ = fn.__doc__
    return pattern


@_daily_only
def weekly_pivot_failure(df, _):
    """Daily bars, fires at a Sunday close: a weekly close above the last confirmed weekly pivot
    high (5 weeks either side), then two consecutive weekly closes back below it."""
    return ind.weekly_on_daily(df)["w_pivot_failure"]


@_daily_only
def band_touch(df, _):
    """Daily low at or below the bull market support band (the higher of the 20-week SMA and
    21-week EMA, from the last completed week) after 28 days whose lows were all above it."""
    top = ind.weekly_on_daily(df)["w_band_top"]
    above = df["low"] > top
    return (df["low"] <= top) & (ind.run_length(above).shift(1) >= 28)


def long_sub200_reclaim(df, i):
    """First close above SMA200 after 180 or more consecutive closes below it, with SMA200
    falling (sma200_change20 < 0) on the reclaim day."""
    below = ind.run_length(df["close"] < i["sma200"])
    return (df["close"] > i["sma200"]) & (below.shift(1) >= 180) & (i["sma200_change20"] < 0)


@_daily_only
def two_weekly_closes_below_sma52(df, _):
    """Daily bars, fires at a Sunday close: the second consecutive weekly close below the
    52-week SMA (our definition of the one-year moving average)."""
    w = ind.weekly_on_daily(df)
    sunday = df.index.day_of_week == 6
    run = w["w_weeks_below_sma52"]
    return pd.Series(sunday, index=df.index) & (run == 2) & (run.shift(1) == 1)  # the day before = last week


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
    "weekly_pivot_failure": weekly_pivot_failure,
    "band_touch": band_touch,
    "long_sub200_reclaim": long_sub200_reclaim,
    "two_weekly_closes_below_sma52": two_weekly_closes_below_sma52,
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
    """Every field a condition can refer to: indicators, regime-v1 columns and, on daily bars,
    `days_below_sma200` and the last completed week's fields (`weekly_on_daily`)."""
    indicators = ind.compute_all(df)
    labels = regime.classify(df)
    out = indicators.join(labels.drop(columns=[c for c in labels.columns if c in indicators.columns]))
    if ind.is_daily(df):
        out["days_below_sma200"] = ind.run_length(df["close"] < indicators["sma200"])
        out = out.join(ind.weekly_on_daily(df).drop(columns="w_pivot_failure"))
    return out


def condition_mask(condition: dict, index: pd.DatetimeIndex, load: Callable[[str], pd.DataFrame],
                   own_market: str, fields_cache: dict | None = None) -> pd.Series:
    """True on days where the condition holds. Days the other market has no data are False.

    `fields_cache` (market -> fields table) avoids recomputing a shared market, e.g. BTC, for
    every market of a pooled backtest.
    """
    unknown = set(condition) - {"market", "field", "op", "value"}
    if unknown or not {"field", "op", "value"} <= set(condition):
        raise SpecError(f"condition needs field, op, value (and optional market): {condition}")
    if condition["op"] not in OPS:
        raise SpecError(f"unknown op {condition['op']!r}; use one of {', '.join(OPS)}")
    market = condition.get("market", own_market)
    if fields_cache is None:
        table = fields(load(market))
    else:
        if market not in fields_cache:
            fields_cache[market] = fields(load(market))
        table = fields_cache[market]
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
