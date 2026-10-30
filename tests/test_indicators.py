"""Indicators feed every later signal, so two properties matter most:
1. they compute what their names say: checked against TA-Lib (an independent C
   implementation) after warm-up, or against hand-worked values where TA-Lib has no match;
2. they never look ahead: a value at day t must not change when later bars change.
A look-ahead leak would make every backtest built on them look better than reality."""

import numpy as np
import pandas as pd
import pytest
import talib

from cryptoagent.quant import indicators as ind

WARMUP = 300  # seeding conventions differ slightly; exponential averages converge well within this


def ohlcv(n=1500, seed=7):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))
    open_ = close * np.exp(rng.normal(0, 0.01, n))
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.015, n)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.015, n)))
    volume = rng.lognormal(10, 0.5, n)
    index = pd.date_range("2018-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index)


DF = ohlcv()
C, H, L, V = (DF[c].to_numpy() for c in ("close", "high", "low", "volume"))


def same(ours, reference):
    np.testing.assert_allclose(np.asarray(ours, dtype=float)[WARMUP:], np.asarray(reference)[WARMUP:], rtol=1e-6)


@pytest.mark.parametrize("ours,reference", [
    (lambda: ind.sma(DF["close"], 50), lambda: talib.SMA(C, 50)),
    (lambda: ind.ema(DF["close"], 200), lambda: talib.EMA(C, 200)),
    (lambda: ind.rsi(DF["close"], 14), lambda: talib.RSI(C, 14)),
    (lambda: ind.macd(DF["close"])["macd"], lambda: talib.MACD(C)[0]),
    (lambda: ind.macd(DF["close"])["macd_signal"], lambda: talib.MACD(C)[1]),
    (lambda: ind.atr(DF, 14), lambda: talib.ATR(H, L, C, 14)),
    (lambda: ind.bollinger(DF["close"])["bb_upper"], lambda: talib.BBANDS(C, 20, 2, 2)[0]),
    (lambda: ind.bollinger(DF["close"])["bb_lower"], lambda: talib.BBANDS(C, 20, 2, 2)[2]),
    (lambda: ind.adx(DF, 14)["adx"], lambda: talib.ADX(H, L, C, 14)),
    (lambda: ind.adx(DF, 14)["plus_di"], lambda: talib.PLUS_DI(H, L, C, 14)),
    (lambda: ind.adx(DF, 14)["minus_di"], lambda: talib.MINUS_DI(H, L, C, 14)),
    (lambda: ind.obv(DF), lambda: talib.OBV(C, V)),
    (lambda: ind.roc(DF["close"], 30), lambda: talib.ROCR(C, 30) - 1),
], ids=["sma", "ema", "rsi", "macd", "macd_signal", "atr", "bb_upper", "bb_lower", "adx", "plus_di", "minus_di",
        "obv", "roc"])
def test_matches_talib_after_warmup(ours, reference):
    same(ours(), reference())


def test_ema_matches_talib_from_its_first_value():
    # Same seeding convention, so plain EMA should match exactly, not only after warm-up.
    np.testing.assert_allclose(ind.ema(DF["close"], 20)[19:], talib.EMA(C, 20)[19:], rtol=1e-12)


def test_hand_worked_values():
    close = pd.Series([100.0, 110.0, 99.0, 120.0, 60.0])
    np.testing.assert_allclose(ind.drawdown_from_ath(close), [0, 0, -0.1, 0, -0.5])
    np.testing.assert_allclose(ind.roc(close, 2)[2:], [-0.01, 120 / 110 - 1, 60 / 99 - 1])
    np.testing.assert_allclose(ind.deviation(close, pd.Series([100.0] * 5)), [0, 0.1, -0.01, 0.2, -0.4])
    # Daily log returns of exactly +/-1% alternating: std over 2 returns, annualised by sqrt(365).
    up_down = pd.Series(100 * np.exp(np.cumsum([0, 0.01, -0.01, 0.01])))
    expected = np.std([0.01, -0.01], ddof=1) * np.sqrt(365)
    assert ind.realized_vol(up_down, 2).iloc[-1] == pytest.approx(expected)
    assert ind.rsi(pd.Series(np.arange(1.0, 30.0)), 14).iloc[-1] == 100  # only gains


def test_pivots_are_reported_on_confirmation_day():
    highs = [1, 2, 3, 9, 3, 2, 1, 2, 3, 4]
    df = pd.DataFrame({"high": highs, "low": [h - 0.5 for h in highs]},
                      index=pd.date_range("2024-01-01", periods=10, freq="D"), dtype=float)
    pivots = ind.confirmed_pivots(df, k=3)
    high = pivots[pivots["kind"] == "high"].iloc[0]
    assert (high["day"], high["confirmed"], high["price"]) == (df.index[3], df.index[6], 9.0)
    last = ind.last_pivots(df, k=3)
    assert last["pivot_high"].isna()[:6].all() and last["pivot_high"].iloc[6] == 9.0  # unknown until day 6


@pytest.mark.parametrize("cut", [250, 700, 1498])
def test_no_lookahead(cut):
    """Values through day `cut` are identical with the true future, a garbage future, or no future."""
    reference = ind.compute_all(DF).iloc[:cut + 1]
    garbage = DF.copy()
    rng = np.random.default_rng(1)
    garbage.iloc[cut + 1:] = rng.uniform(1, 1e6, size=garbage.iloc[cut + 1:].shape)
    garbage["high"] = garbage[["open", "high", "low", "close"]].max(axis=1)
    garbage["low"] = garbage[["open", "high", "low", "close"]].min(axis=1)
    pd.testing.assert_frame_equal(ind.compute_all(garbage).iloc[:cut + 1], reference)
    pd.testing.assert_frame_equal(ind.compute_all(DF.iloc[:cut + 1]), reference)


def test_weekly_volatility_is_annualised_with_weekly_bars():
    """Annualising weekly returns with sqrt(365) overstated weekly volatility by ~2.6x (found in review)."""
    weekly = DF.resample("W-MON", label="left", closed="left").agg({"close": "last"})
    expected = np.log(weekly["close"]).diff().rolling(30).std() * np.sqrt(365 / 7)
    np.testing.assert_allclose(ind.realized_vol(weekly["close"], 30).dropna(), expected.dropna())
    assert ind.bars_per_year(DF.index) == 365
