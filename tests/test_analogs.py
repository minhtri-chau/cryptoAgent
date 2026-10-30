"""Analogue search turns "this looks like 2019" into numbers, so it is only honest if:
the past it compares with is genuinely past (no forward window reaches beyond the as-of
day, no state uses later bars), each neighbour is a separate episode (so the sample size is
real), it finds look-alike episodes that are actually there, and its value over the plain
base rate is measured out of sample and recorded, with the test period sealed."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from cryptoagent import store, trials
from cryptoagent.quant import analogs


def frame(close, start="2018-01-01"):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1.0},
                        index=pd.date_range(start, periods=len(close), freq="D"))


def walk(n, seed, sigma=0.03):
    return 100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0.0005, sigma, n)))


DF = frame(walk(1500, 1))


def test_uses_only_the_past_and_precomputed_states_are_equivalent():
    cut, horizon = 1100, 30
    direct = analogs.search(DF.iloc[:cut + 1], horizon, 10)
    garbage = DF.copy()
    garbage.iloc[cut + 1:] = np.random.default_rng(2).uniform(1, 1e6, size=garbage.iloc[cut + 1:].shape)
    reused = analogs.search(DF.iloc[:cut + 1], horizon, 10, precomputed=analogs.states(garbage))
    assert reused == direct  # states are causal, so walk-forward may compute them once
    as_of = DF.index[cut]
    for n in direct["neighbours"]:
        assert pd.Timestamp(n["day"]) + pd.Timedelta(days=horizon) <= as_of  # whole outcome known by as_of


def test_neighbours_are_separate_episodes():
    result = analogs.search(DF, 30, 20)
    days = sorted(pd.Timestamp(n["day"]) for n in result["neighbours"])
    assert result["effective_n"] == len(days) == 20
    assert all((b - a).days >= 30 for a, b in zip(days, days[1:]))


def test_finds_planted_lookalike_episodes():
    """A price path that repeats the same 400-day loop four times: the ends of loops 2 and 3
    are in exactly today's state (end of loop 4), so they must be the two closest analogues.
    (Drawdown from the high and the long EMA depend on the whole path, so the copies must
    repeat the whole path, not just a shape at a different price level.)"""
    rng = np.random.default_rng(7)
    loop = np.cumsum(rng.normal(0, 0.04, 400))
    loop -= np.linspace(0, loop[-1], 400)  # closes the loop: each copy starts where the last ended
    df = frame(100 * np.exp(np.tile(loop, 4)))
    result = analogs.search(df, 30, 3)
    planted = {df.index[2 * 400 - 1].date().isoformat(), df.index[3 * 400 - 1].date().isoformat()}
    assert {n["day"] for n in result["neighbours"][:2]} == planted
    assert result["neighbours"][1]["distance"] < 0.05


def test_refuses_without_enough_history():
    with pytest.raises(analogs.AnalogError, match="incomplete|candidate"):
        analogs.search(DF.iloc[:300], 30, 10)


def test_walk_forward_scores_both_methods_on_non_overlapping_dates():
    result = analogs.evaluate(DF, 30, 10, DF.index[900])
    days = [pd.Timestamp(d["day"]) for d in result["dates"]]
    assert days[0] >= DF.index[900] and all((b - a).days == 30 for a, b in zip(days, days[1:]))
    assert days[-1] + pd.Timedelta(days=30) <= DF.index[-1]  # every outcome is inside the data
    rows = pd.DataFrame(result["dates"])
    assert result["brier_analog"] == pytest.approx(round(((rows["p_analog"] - rows["up"]) ** 2).mean(), 6))
    assert result["brier_skill_vs_base"] == pytest.approx(1 - result["brier_analog"] / result["brier_base"], abs=1e-5)


def test_analog_trials_are_recorded_and_the_test_period_is_sealed(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    market_id = store.get_or_create_market(conn, source_id, "BTC", "USD", "BTC/USD", "BTC-USD")
    run_id = store.start_run(conn, source_id, {})
    df = frame(walk(2900, 3), start="2017-01-01")  # through 2024-12-09
    with conn:
        conn.executemany("INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                         " VALUES (?, '1d', ?, ?, ?, ?, ?, 1, ?)",
                         [(market_id, d.strftime("%Y-%m-%dT00:00:00Z"), r.open, r.high, r.low, r.close, run_id)
                          for d, r in df.iterrows()])
    args = ("coinbase:BTC/USD", 30, 10)
    with pytest.raises(sqlite3.IntegrityError, match="validation trial"):
        trials.run_analog_trial(conn, *args, "test", "analog-btc", "h", "claude")
    val = trials.run_analog_trial(conn, *args, "validation", "analog-btc", "h", "claude")
    assert val["spec"]["method"] == "analog-v1" and val["evaluations"] > 20
    assert val["last_day"] <= "2024-05-31"  # outcome of the last date (+30 days) inside the period
    trials.run_analog_trial(conn, *args, "test", "analog-btc", "h", "claude")
    with pytest.raises(sqlite3.IntegrityError):
        trials.run_analog_trial(conn, *args, "test", "analog-btc", "h", "claude")
    assert trials.show(conn, val["trial_id"])["results"]["dates"]
