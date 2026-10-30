"""regime-v1 labels describe the market to Claude and condition baseline predictions, so they
must follow their written definition exactly and never use information from after day t."""

import numpy as np
import pandas as pd
import pytest

from cryptoagent.quant import regime


def frame(close, start="2020-01-01"):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0},
                        index=pd.date_range(start, periods=len(close), freq="D"))


def test_trend_labels_follow_the_definition():
    up = regime.classify(frame(np.linspace(100, 300, 400)))
    down = regime.classify(frame(np.linspace(300, 100, 400)))
    assert up["trend"].iloc[:219].isna().all()  # SMA200 needs 200 days, its 20-day change 20 more
    assert up["trend"].iloc[-1] == "uptrend" and down["trend"].iloc[-1] == "downtrend"
    # A rally above a still-falling SMA200 is not yet an uptrend.
    rebound = regime.classify(frame(np.r_[np.linspace(300, 100, 400), np.linspace(100, 250, 15)]))
    assert rebound["close"].iloc[-1] > rebound["sma200"].iloc[-1] and rebound["sma200_change20"].iloc[-1] < 0
    assert rebound["trend"].iloc[-1] == "transition"


def test_volatility_percentile_uses_only_earlier_days():
    rng = np.random.default_rng(3)
    calm = rng.normal(0, 0.005, 500)
    wild = rng.normal(0, 0.08, 60)
    out = regime.classify(frame(100 * np.exp(np.cumsum(np.r_[calm, wild]))))
    first_label = out["volatility"].first_valid_index()
    assert out.index.get_loc(first_label) == 30 + regime.MIN_VOL_HISTORY  # 30 days for vol, then 365 of history
    assert out["volatility"].iloc[-1] == "high" and out["vol30_percentile"].iloc[-1] > 0.99


def test_ema200_band_is_the_deepest_reached():
    out = regime.classify(frame(np.r_[np.full(300, 100.0), [61.0, 39.0, 24.0]]))
    assert list(out["ema200_band"].iloc[-3:]) == ["-37.5%", "-60.0%", "-75.0%"]
    assert pd.isna(out["ema200_band"].iloc[299])


@pytest.mark.parametrize("day,cycle,since", [
    ("2012-11-27", 0, None), ("2012-11-28", 1, 0), ("2016-07-08", 1, 1318), ("2024-04-20", 4, 0),
    ("2026-10-29", 4, 922),
])
def test_halving_cycle(day, cycle, since):
    snap = regime.snapshot(frame(np.full(5, 100.0), start=pd.Timestamp(day) - pd.Timedelta(days=4)))
    assert snap["as_of"] == day and snap["halving_cycle"] == cycle and snap["days_since_halving"] == since


def test_no_lookahead():
    rng = np.random.default_rng(5)
    df = frame(100 * np.exp(np.cumsum(rng.normal(0, 0.03, 900))))
    cut = 700
    reference = regime.classify(df).iloc[:cut + 1]
    garbage = df.copy()
    garbage.iloc[cut + 1:] = rng.uniform(1, 1e6, size=garbage.iloc[cut + 1:].shape)
    pd.testing.assert_frame_equal(regime.classify(garbage).iloc[:cut + 1], reference)
    pd.testing.assert_frame_equal(regime.classify(df.iloc[:cut + 1]), reference)
