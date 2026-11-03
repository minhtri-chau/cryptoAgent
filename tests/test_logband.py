"""logband-v1 is the long-term log-regression band outside analysts read as "fair value" and
"accumulation" zones. The version on their charts is fitted once over all history, so past
touches use prices that came later. Ours must refit each day on closes known at that day,
recover a known power law exactly, and be usable as a `--when` field so the idea can be tested."""

import numpy as np
import pandas as pd
import pytest

from cryptoagent.quant import logband, patterns


def power_law(days: int, start="2012-01-01", anchor="2009-01-03", a=-17.0, b=5.8, noise=None):
    index = pd.date_range(start, periods=days, freq="D")
    x = np.log10((index - pd.Timestamp(anchor)).days.to_numpy(dtype=float))
    y = a + b * x + (0 if noise is None else noise)
    return pd.Series(10 ** y, index=index)


def test_recovers_a_known_power_law_and_its_spread():
    # Residuals alternate +/-0.1 (log10), so sigma is about 0.1 and z is about +/-1.
    days = 2000
    noise = np.where(np.arange(days) % 2 == 0, 0.1, -0.1)
    out = logband.fit(power_law(days, noise=noise), "2009-01-03")
    last = out.iloc[-1]
    assert last["lb_slope"] == pytest.approx(5.8, abs=0.05)
    assert last["lb_sigma"] == pytest.approx(0.1, abs=0.002)
    assert abs(last["lb_z"]) == pytest.approx(1.0, abs=0.03)
    assert last["lb_upper2"] / last["lb_mid"] == pytest.approx(10 ** (2 * last["lb_sigma"]))


def test_nothing_before_three_years_of_closes():
    out = logband.fit(power_law(1200), "2009-01-03")
    assert out["lb_mid"].iloc[:logband.MIN_DAYS - 1].isna().all()
    assert out["lb_mid"].iloc[logband.MIN_DAYS - 1:].notna().all()


@pytest.mark.parametrize("cut", [1100, 1500, 1999])
def test_no_lookahead(cut):
    """A band on day `cut` is the same with the true future, a garbage future, or no future."""
    rng = np.random.default_rng(1)
    close = power_law(2000, noise=rng.normal(0, 0.2, 2000))
    garbage = close.copy()
    garbage.iloc[cut + 1:] = rng.uniform(1, 1e7, len(close) - cut - 1)
    truth = logband.fit(close, "2009-01-03").iloc[:cut + 1]
    pd.testing.assert_frame_equal(truth, logband.fit(garbage, "2009-01-03").iloc[:cut + 1])
    pd.testing.assert_frame_equal(truth, logband.fit(close.iloc[:cut + 1], "2009-01-03"))


def test_hindsight_fit_differs_from_the_point_in_time_band():
    # A slope change after day 1500 moves the full-history fit's view of day 1500, not ours.
    close = power_law(3000)
    close.iloc[1500:] = close.iloc[1500:] * np.linspace(1, 20, 1500)
    ours = logband.fit(close, "2009-01-03")["lb_mid"].iloc[1499]
    hindsight = logband.fit(close, "2009-01-03")["lb_mid"].iloc[-1]
    assert ours == pytest.approx(logband.fit(close.iloc[:1500], "2009-01-03")["lb_mid"].iloc[-1])
    assert ours != pytest.approx(hindsight)


def test_anchor_is_genesis_for_btc_and_eth_else_first_candle():
    assert logband.anchor_for("bitstamp:BTC/USD") == "2009-01-03"
    assert logband.anchor_for("coinbase:ETH/USD") == "2015-07-30"
    assert logband.anchor_for("ETH/BTC") == "2015-07-30"
    assert logband.anchor_for("coinbase:SOL/USD") is None
    with pytest.raises(ValueError):
        logband.fit(power_law(1200), "2013-01-01")  # anchor after the first close


def test_ratio_uses_only_days_both_markets_have():
    a = pd.DataFrame({"close": [10.0, 20.0, 30.0]}, index=pd.date_range("2020-01-01", periods=3))
    b = pd.DataFrame({"close": [5.0, 10.0]}, index=pd.date_range("2020-01-02", periods=2))
    assert logband.ratio(a, b).tolist() == [4.0, 3.0]


def test_band_is_a_condition_field_on_daily_bars():
    close = power_law(1200, noise=np.random.default_rng(2).normal(0, 0.1, 1200))
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0})
    table = patterns.fields(df, "bitstamp:BTC/USD")
    assert {"lb_z", "lb_mid", "lb_lower2"} <= set(table.columns)
    # anchored on BTC's genesis because the market is named, not on the first candle
    pd.testing.assert_series_equal(table["lb_z"], logband.fit(close, "2009-01-03")["lb_z"])
