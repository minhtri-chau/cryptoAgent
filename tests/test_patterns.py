"""Patterns are the vocabulary the researcher tests hypotheses with. Each must mean exactly
what its definition says and be knowable at the close of its day: a setup that secretly
uses the next bar would make every backtest of it a fantasy."""

import numpy as np
import pandas as pd
import pytest

from cryptoagent.quant import indicators as ind
from cryptoagent.quant import patterns
from cryptoagent.quant.series import to_4h, to_weekly


def frame(close, high=None, low=None, start="2020-01-01"):
    close = np.asarray(close, dtype=float)
    high = close if high is None else np.asarray(high, dtype=float)
    low = close if low is None else np.asarray(low, dtype=float)
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": 1.0},
                        index=pd.date_range(start, periods=len(close), freq="D"))


def test_breakout_and_breakdown_use_previous_bars_only():
    close = [10.0] * 25 + [11.0, 9.0]
    high = [10.5] * 25 + [11.0, 9.0]
    low = [9.5] * 25 + [11.0, 9.0]
    df = frame(close, high, low)
    up = patterns.evaluate("breakout_20", df)
    down = patterns.evaluate("breakdown_20", df)
    assert up.iloc[25] and not up.iloc[:25].any()   # 11 > 10.5, the prior 20-day high
    assert down.iloc[26] and not down.iloc[:26].any()  # 9 < 9.5, the prior 20-day low


def test_pullback_to_sma50_in_uptrend():
    close = np.r_[np.linspace(50, 150, 300), [140.0]]
    low = close.copy()
    df = frame(close, low=low)
    sma50 = df["close"].rolling(50).mean().iloc[-1]
    df.iloc[-1, df.columns.get_loc("low")] = sma50 * 1.01  # within 2% of SMA50
    df.iloc[-1, df.columns.get_loc("close")] = sma50 * 1.05
    assert patterns.evaluate("pullback_sma50", df).iloc[-1]
    df.iloc[-1, df.columns.get_loc("low")] = sma50 * 1.04  # not close enough
    assert not patterns.evaluate("pullback_sma50", df).iloc[-1]


def test_ema_bands_and_oversold_uptrend():
    crash = frame(np.r_[np.full(300, 100.0), [70.0, 60.0, 35.0]])
    assert list(patterns.evaluate("ema200_band_37", crash).iloc[-3:]) == [False, True, True]
    assert list(patterns.evaluate("ema200_band_60", crash).iloc[-3:]) == [False, False, True]
    dip = frame(np.r_[np.linspace(50, 200, 300), np.linspace(199, 170, 15)])
    assert patterns.evaluate("oversold_uptrend", dip).iloc[-1]  # RSI collapses, still above SMA200
    assert not patterns.evaluate("oversold_uptrend", crash).iloc[-1]  # oversold but below SMA200


def test_higher_highs_lows_needs_both_rising():
    wave = np.r_[np.tile(np.r_[np.linspace(10, 20, 10), np.linspace(20, 10, 10)[1:-1]], 3)]
    rising = wave + np.repeat(np.arange(3) * 5.0, len(wave) // 3)
    assert patterns.evaluate("higher_highs_lows", frame(rising)).iloc[-1]
    assert not patterns.evaluate("higher_highs_lows", frame(wave)).iloc[-1]  # equal pivots are not higher


def test_squeeze_flags_unusually_narrow_bands():
    rng = np.random.default_rng(2)
    close = 100 * np.exp(np.cumsum(np.r_[rng.normal(0, 0.03, 420), rng.normal(0, 0.001, 30)]))
    signal = patterns.evaluate("squeeze", frame(close))
    assert signal.iloc[-1] and not signal.iloc[:385].any()  # needs 365 bars of bandwidth history


def weekly_frame(weekly_closes, start="2020-01-06"):
    """Daily bars where every day of week k closes at weekly_closes[k] (weeks start on Monday)."""
    return frame(np.repeat(np.asarray(weekly_closes, dtype=float), 7), start=start)


def test_weekly_fields_are_last_completed_week_only():
    df = weekly_frame(np.arange(1.0, 31.0))
    w = ind.weekly_on_daily(df)
    assert np.isnan(w["w_close"].iloc[5])            # Saturday of week 1: no week completed yet
    assert w["w_close"].iloc[6] == 1.0               # its Sunday close completes week 1
    assert (w["w_close"].iloc[6:13] == 1.0).all()     # held from that Sunday through the next Saturday
    assert w["w_close"].iloc[13] == 2.0
    assert w["w_sma20"].iloc[20 * 7 - 1] == pytest.approx(np.mean(np.arange(1.0, 21.0)))


def test_weekly_pivot_failure_fires_on_the_second_close_back_below():
    # Rise to a weekly high of 100 (week 10), five lower weeks confirm it as a pivot, then a close
    # above it (105), then two closes below (95, 95): fires on the Sunday ending week 18 only.
    closes = np.r_[np.linspace(50, 95, 10), [100.0], np.full(5, 90.0), [105.0, 95.0, 95.0, 95.0]]
    df = weekly_frame(closes)
    signal = patterns.evaluate("weekly_pivot_failure", df)
    assert list(signal[signal].index) == [df.index[19 * 7 - 1]]
    one_below = weekly_frame(np.r_[closes[:18], [110.0, 110.0]])  # only one week below: no failure
    assert not patterns.evaluate("weekly_pivot_failure", one_below).any()


def test_band_touch_needs_four_weeks_clear_above_the_band():
    close = np.linspace(50, 150, 300)
    df = frame(close, low=close * 0.999)
    top = ind.weekly_on_daily(df)["w_band_top"]
    df.iloc[-1, df.columns.get_loc("low")] = top.iloc[-1] * 0.99   # today's low dips into the band
    signal = patterns.evaluate("band_touch", df)
    assert signal.iloc[-1] and signal.iloc[:-1].sum() == 0
    df.iloc[-5, df.columns.get_loc("low")] = top.iloc[-5] * 0.99   # an earlier touch within 28 days
    assert not patterns.evaluate("band_touch", df).iloc[-1]


def test_long_sub200_reclaim_needs_180_days_below_a_falling_average():
    falling = np.r_[np.full(250, 100.0), np.linspace(99, 50, 200)]
    df = frame(np.r_[falling, [95.0]])                 # 200 closes below, SMA200 still falling, close jumps above
    assert patterns.evaluate("long_sub200_reclaim", df).iloc[-1]
    short = frame(np.r_[np.full(250, 100.0), np.linspace(99, 80, 100), [99.5]])  # only 100 days below
    assert not patterns.evaluate("long_sub200_reclaim", short).any()


def test_two_weekly_closes_below_sma52():
    df = weekly_frame(np.r_[np.full(60, 100.0) + np.arange(60) * 0.1, [90.0, 90.0, 90.0]])
    signal = patterns.evaluate("two_weekly_closes_below_sma52", df)
    assert list(signal[signal].index) == [df.index[62 * 7 - 1]]


def test_weekly_patterns_never_fire_on_weekly_bars():
    weekly = to_weekly(weekly_frame(np.r_[np.linspace(50, 95, 10), [100.0], np.full(5, 90.0), [105.0, 95.0, 95.0]]))
    assert not patterns.evaluate("weekly_pivot_failure", weekly.drop(columns="days")).any()


def test_weekly_fields_have_no_lookahead():
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, 900)))
    df = frame(close, close * 1.02, close * 0.98)
    cut = 700
    reference = ind.weekly_on_daily(df).iloc[:cut + 1]
    garbage = df.copy()
    noise = rng.uniform(1, 1e6, size=len(df) - cut - 1)
    for col in ("open", "close", "high", "low"):
        garbage.iloc[cut + 1:, garbage.columns.get_loc(col)] = noise
    pd.testing.assert_frame_equal(ind.weekly_on_daily(garbage).iloc[:cut + 1], reference)
    pd.testing.assert_frame_equal(ind.weekly_on_daily(df.iloc[:cut + 1]), reference)


@pytest.mark.parametrize("name", list(patterns.PATTERNS))
def test_no_lookahead(name):
    rng = np.random.default_rng(9)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, 900)))
    high, low = close * 1.02, close * 0.98
    df = frame(close, high, low)
    cut = 700
    reference = patterns.evaluate(name, df).iloc[:cut + 1]
    garbage = df.copy()
    noise = rng.uniform(1, 1e6, size=len(df) - cut - 1)
    for col in ("open", "close"):
        garbage.iloc[cut + 1:, garbage.columns.get_loc(col)] = noise
    garbage.iloc[cut + 1:, garbage.columns.get_loc("high")] = noise * 1.5
    garbage.iloc[cut + 1:, garbage.columns.get_loc("low")] = noise / 1.5
    pd.testing.assert_series_equal(patterns.evaluate(name, garbage).iloc[:cut + 1], reference)
    pd.testing.assert_series_equal(patterns.evaluate(name, df.iloc[:cut + 1]), reference)


def test_condition_on_another_market_uses_same_day_and_unknown_days_are_false():
    df = frame(np.linspace(100, 200, 300))
    btc = frame(np.r_[np.linspace(100, 50, 150), np.linspace(50, 300, 140)])  # 10 days shorter
    mask = patterns.condition_mask({"market": "btc", "field": "close", "op": ">", "value": 60}, df.index,
                                   {"btc": btc}.__getitem__, "own")
    assert mask.iloc[0] and not mask.iloc[140] and mask.iloc[289] and not mask.iloc[-1]
    with pytest.raises(patterns.SpecError, match="unknown field"):
        patterns.condition_mask({"field": "nope", "op": ">", "value": 0}, df.index, {"own": df}.__getitem__, "own")


def test_weekly_bars_are_complete_weeks_only():
    daily = frame(np.arange(1.0, 18.0), start="2024-01-01")  # Mon 1 Jan .. Wed 17 Jan
    weekly = to_weekly(daily)
    assert list(weekly.index.strftime("%Y-%m-%d")) == ["2024-01-01", "2024-01-08"]  # week of 15 Jan unfinished
    first = weekly.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"], first["days"]) == \
        (1.0, 7.0, 1.0, 7.0, 7.0, 7)


def test_4h_bars_come_from_complete_hourly_candles_only():
    hours = pd.date_range("2024-01-01 00:00", periods=10, freq="h")  # 00:00 .. 09:00
    hourly = pd.DataFrame({"open": range(1, 11), "high": range(1, 11), "low": range(1, 11), "close": range(1, 11),
                           "volume": 1.0}, index=hours, dtype=float)
    bars = to_4h(hourly)
    assert list(bars.index.strftime("%H:%M")) == ["00:00", "04:00"]  # 08:00 bar has only 2 of 4 hours
    assert tuple(bars.iloc[1][["open", "high", "low", "close", "volume"]]) == (5.0, 8.0, 5.0, 8.0, 4.0)
