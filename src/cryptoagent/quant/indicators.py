"""Technical indicators over a daily OHLCV frame (columns open, high, low, close, volume).

Every function is causal: its value at day t uses only bars up to and including t
(tests/test_indicators.py checks this by corrupting future bars). Smoothing follows
TA-Lib's conventions (EMA and Wilder averages seeded with a simple mean of the first n
values), and TA-Lib is used in tests as the reference; after warm-up the two agree.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DAYS_PER_YEAR = 365  # crypto trades every day


def _seeded_smoothing(values: pd.Series, alpha: float, n: int) -> pd.Series:
    """Exponential smoothing seeded with the mean of the first n valid values (TA-Lib style)."""
    x = values.to_numpy(dtype=float)
    out = np.full(len(x), np.nan)
    valid = np.flatnonzero(~np.isnan(x))
    if len(valid) < n:
        return pd.Series(out, index=values.index)
    start = valid[0] + n - 1
    out[start] = x[valid[0]:start + 1].mean()
    for i in range(start + 1, len(x)):
        out[i] = out[i - 1] + alpha * (x[i] - out[i - 1])
    return pd.Series(out, index=values.index)


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    return _seeded_smoothing(series, 2 / (n + 1), n)


def wilder(series: pd.Series, n: int) -> pd.Series:
    """Wilder's moving average (alpha = 1/n), used by RSI, ATR and ADX."""
    return _seeded_smoothing(series, 1 / n, n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    change = close.diff()
    gain = wilder(change.clip(lower=0), n)
    loss = wilder(-change.clip(upper=0), n)
    out = 100 - 100 / (1 + gain / loss)
    return out.where(loss != 0, 100.0).where(gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    signal_line = ema(line, signal)
    return pd.DataFrame({"macd": line, "macd_signal": signal_line, "macd_hist": line - signal_line})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
                       axis=1)
    return ranges.max(axis=1).where(prev_close.notna())


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    width = k * close.rolling(n).std(ddof=0)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": mid + width, "bb_lower": mid - width})


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0).where(up.notna())
    minus_dm = down.where((down > up) & (down > 0), 0.0).where(down.notna())
    tr = wilder(true_range(df), n)
    plus_di = 100 * wilder(plus_dm, n) / tr
    minus_di = 100 * wilder(minus_dm, n) / tr
    total = plus_di + minus_di
    dx = (100 * (plus_di - minus_di).abs() / total).where(total != 0, 0.0).where(total.notna())
    return pd.DataFrame({"adx": wilder(dx, n), "plus_di": plus_di, "minus_di": minus_di})


def obv(df: pd.DataFrame) -> pd.Series:
    """On-balance volume, starting from the first bar's volume (as TA-Lib does)."""
    direction = np.sign(df["close"].diff()).fillna(0)
    signed = df["volume"] * direction
    signed.iloc[0] = df["volume"].iloc[0]
    return signed.cumsum()


def roc(close: pd.Series, n: int) -> pd.Series:
    """Rate of change as a fraction: close / close n days ago - 1."""
    return close / close.shift(n) - 1


def bars_per_year(index: pd.DatetimeIndex) -> float:
    """365 for daily bars, 52.18 weekly, 8760 hourly: inferred from the typical spacing of the index."""
    if len(index) < 2 or not isinstance(index, pd.DatetimeIndex):
        return float(DAYS_PER_YEAR)
    spacing_days = pd.Series(index).diff().dt.total_seconds().median() / 86_400
    return DAYS_PER_YEAR / spacing_days


def realized_vol(close: pd.Series, n: int = 30) -> pd.Series:
    """Annualised standard deviation of log returns over n bars (daily or weekly)."""
    return np.log(close).diff().rolling(n).std() * np.sqrt(bars_per_year(close.index))


def drawdown_from_ath(close: pd.Series) -> pd.Series:
    """Close relative to the highest close so far (0 at a new high, -0.5 = 50% below)."""
    return close / close.cummax() - 1


def deviation(close: pd.Series, average: pd.Series) -> pd.Series:
    return close / average - 1


def confirmed_pivots(df: pd.DataFrame, k: int = 5) -> pd.DataFrame:
    """Swing highs and lows, each reported on the day it is confirmed.

    A pivot high at day i has a high strictly above every other high in [i-k, i+k]; it can
    only be known k days later, so `confirmed` is day i+k and `day` is the pivot itself.
    """
    events = []
    for col, kind in (("high", "high"), ("low", "low")):
        x = df[col].to_numpy()
        for i in range(k, len(x) - k):
            window = np.concatenate([x[i - k:i], x[i + 1:i + k + 1]])
            if (kind == "high" and x[i] > window.max()) or (kind == "low" and x[i] < window.min()):
                events.append({"confirmed": df.index[i + k], "day": df.index[i], "kind": kind, "price": x[i]})
    return pd.DataFrame(events, columns=["confirmed", "day", "kind", "price"]).sort_values(
        ["confirmed", "kind"], ignore_index=True)


def last_pivots(df: pd.DataFrame, k: int = 5) -> pd.DataFrame:
    """For each day, the price of the most recent pivot high and low confirmed by that day."""
    events = confirmed_pivots(df, k)
    out = pd.DataFrame(index=df.index, columns=["pivot_high", "pivot_low"], dtype=float)
    for kind in ("high", "low"):
        e = events[events["kind"] == kind].groupby("confirmed")["price"].last()
        out[f"pivot_{kind}"] = e.reindex(df.index).ffill()
    return out


def run_length(mask: pd.Series) -> pd.Series:
    """Consecutive True values ending at each bar (0 where False)."""
    mask = mask.fillna(False).astype(bool)
    return mask.astype(int).groupby((~mask).cumsum()).cumsum()


def is_daily(df: pd.DataFrame) -> bool:
    return len(df) > 1 and pd.Series(df.index).diff().median() == pd.Timedelta(days=1)


def weekly_on_daily(daily: pd.DataFrame) -> pd.DataFrame:
    """Weekly indicators as known at each day's close: the values of the last completed week.

    Weeks run Monday to Sunday (`series.to_weekly`), so a week is known at its Sunday close
    and its values hold until the next Sunday. Days before the first completed week are NaN.
    - w_close, w_sma20 (20-week SMA), w_ema21 (21-week EMA), w_sma52 (52-week SMA, our
      definition of a "one-year moving average")
    - w_band_top: the higher of w_sma20 and w_ema21 (the "bull market support band")
    - w_pivot_high: the last weekly pivot high confirmed by that week (5 weeks either side)
    - w_weeks_below_pivot_high / w_weeks_below_sma52: consecutive weekly closes below each
    - w_pivot_failure_days_ago: days since the last `weekly_pivot_failure` (NaN if none yet)
    """
    from cryptoagent.quant.series import to_weekly  # series has no indicator dependency

    weekly = to_weekly(daily)
    close = weekly["close"]
    w = pd.DataFrame(index=weekly.index)
    w["w_close"] = close
    w["w_sma20"] = sma(close, 20)
    w["w_ema21"] = ema(close, 21)
    w["w_sma52"] = sma(close, 52)
    w["w_band_top"] = w[["w_sma20", "w_ema21"]].max(axis=1, skipna=False)
    w["w_pivot_high"] = last_pivots(weekly, 5)["pivot_high"]
    w["w_weeks_below_pivot_high"] = run_length(close < w["w_pivot_high"])
    w["w_weeks_below_sma52"] = run_length(close < w["w_sma52"])
    # A failed breakout: a weekly close above the pivot high, then two consecutive closes below it.
    failure = (w["w_weeks_below_pivot_high"] == 2) & (close.shift(2) > w["w_pivot_high"])
    w.index = w.index + pd.Timedelta(days=6)  # known at the week's Sunday close
    out = w.reindex(daily.index, method="ffill") if len(w) else w.reindex(daily.index)
    last_failure = pd.Series(w.index[failure.to_numpy()], index=w.index[failure.to_numpy()])
    last_failure = last_failure.reindex(daily.index).ffill()
    out["w_pivot_failure_days_ago"] = (pd.Series(daily.index, index=daily.index) - last_failure).dt.days
    out["w_pivot_failure"] = pd.Series(failure.to_numpy(), index=w.index).reindex(daily.index).fillna(False).astype(bool)
    return out


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """The standard indicator set used by reports, patterns and regime classification.

    Periods are counted in bars: on weekly bars `sma50` is 50 weeks and `roc7` is 7 weeks.
    Only `vol30` is annualised, using the bar spacing.
    """
    close = df["close"]
    out = pd.DataFrame(index=df.index)
    out["close"] = close
    for n in (20, 50, 200):
        out[f"sma{n}"] = sma(close, n)
    for n in (50, 200):
        out[f"sma{n}_change20"] = out[f"sma{n}"] / out[f"sma{n}"].shift(20) - 1  # slope over 20 bars
    out["ema200"] = ema(close, 200)
    out["ema200_dev"] = deviation(close, out["ema200"])
    out["rsi14"] = rsi(close, 14)
    out = out.join(macd(close))
    out["atr14"] = atr(df, 14)
    out["atr14_pct"] = out["atr14"] / close
    out = out.join(bollinger(close))
    out = out.join(adx(df, 14))
    out["obv"] = obv(df)
    for n in (7, 30, 90, 365):
        out[f"roc{n}"] = roc(close, n)
    out["vol30"] = realized_vol(close, 30)
    out["drawdown_ath"] = drawdown_from_ath(close)
    return out.join(last_pivots(df, 5))
