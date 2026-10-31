"""ML is only admitted if it beats the base rate out of sample. That verdict is only meaningful
if the evaluation (a) never trains on outcomes that were unknown at the forecast date, and (b) can
actually tell a real, learnable relationship from noise: a test that always says "no skill" would
be as useless as one that always says "skill"."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from cryptoagent import store, trials
from cryptoagent.quant import ml


def frame(close, start="2015-01-01"):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1.0},
                        index=pd.date_range(start, periods=len(close), freq="D"))


def trending(n=2600, seed=0):
    """Long runs of up or down drift: recent momentum genuinely predicts the next 10 days."""
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([-0.012, 0.012], size=n // 80 + 1), 80)[:n]
    return frame(100 * np.exp(np.cumsum(drift + rng.normal(0, 0.006, n))))


def random_walk(n=2600, seed=1):
    return frame(100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, 0.03, n))))


@pytest.mark.parametrize("kind", list(ml.MODELS))
def test_never_trains_on_outcomes_unknown_at_the_forecast_date(kind):
    df = random_walk(1500)
    result = ml.evaluate(df, kind, 10, df.index[1100])
    assert result["_purge_check"]
    for pos, latest_train in result["_purge_check"]:
        assert latest_train + 10 <= pos


@pytest.mark.parametrize("kind", list(ml.MODELS))
def test_detects_a_real_learnable_relationship(kind):
    df = trending()
    result = ml.evaluate(df, kind, 10, df.index[1500])
    assert result["evaluations"] >= 90
    assert result["brier_skill_vs_base"] > 0.1 and result["brier_diff_ci90"][0] > 0


@pytest.mark.parametrize("kind", list(ml.MODELS))
def test_finds_no_significant_skill_in_noise(kind):
    df = random_walk()
    result = ml.evaluate(df, kind, 10, df.index[1500])
    assert result["brier_diff_ci90"][0] <= 0  # not distinguishable from the base rate (or worse)


def test_ml_trials_are_recorded_and_sealed(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    market_id = store.get_or_create_market(conn, source_id, "BTC", "USD", "BTC/USD", "BTC-USD")
    run_id = store.start_run(conn, source_id, {})
    df = random_walk(3800)  # 2015-01-01 .. 2025-05-26
    with conn:
        conn.executemany("INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                         " VALUES (?, '1d', ?, ?, ?, ?, ?, 1, ?)",
                         [(market_id, d.strftime("%Y-%m-%dT00:00:00Z"), r.open, r.high, r.low, r.close, run_id)
                          for d, r in df.iterrows()])
    args = ("coinbase:BTC/USD", "logit", 30)
    with pytest.raises(sqlite3.IntegrityError, match="validation trial"):
        trials.run_ml_trial(conn, *args, "test", "ml-btc", "h", "claude")
    val = trials.run_ml_trial(conn, *args, "validation", "ml-btc", "h", "claude")
    assert val["spec"] == {"method": "ml-logit-v1", "market": "coinbase:BTC/USD", "horizon_days": 30}
    assert val["evaluations"] > 20 and "_purge_check" not in val
    trials.run_ml_trial(conn, *args, "test", "ml-btc", "h", "claude")
    with pytest.raises(sqlite3.IntegrityError):
        trials.run_ml_trial(conn, *args, "test", "ml-btc", "h", "claude")
    with pytest.raises(trials.SpecError, match="unknown model"):
        trials.run_ml_trial(conn, "coinbase:BTC/USD", "transformer", 30, "train", "ml-btc", "h", "claude")
