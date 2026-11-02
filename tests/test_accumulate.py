"""Accumulation rules answer "how should a fixed amount go in?", so each rule must buy exactly
when and how its definition says, pay realistic costs, never act on a price it could not have
seen, and be judged against simply buying everything at once."""

import numpy as np
import pandas as pd
import pytest

from cryptoagent.quant import accumulate as acc
from cryptoagent.quant.backtest import Costs
from cryptoagent.quant.patterns import SpecError

FREE = Costs(fee=0.0, slippage=0.0)


def frame(close, opens=None, start="2018-01-01"):
    close = np.asarray(close, dtype=float)
    opens = close if opens is None else np.asarray(opens, dtype=float)
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, close), "low": np.minimum(opens, close),
                         "close": close, "volume": 1.0}, index=pd.date_range(start, periods=len(close), freq="D"))


def test_lump_buys_everything_at_the_next_open_after_costs():
    df = frame([100.0] * 10, opens=[100.0, 110.0] + [100.0] * 8)
    r = acc.simulate(df, 0, {"kind": "lump"}, Costs(fee=0.01, slippage=0.0), 0.0, 5)
    assert r["avg_price_ratio"] == pytest.approx(110 * 1.01 / 100)   # filled at day 1's open, fee on top
    assert r["deployed_4w"] == pytest.approx(1.0)


def test_time_rule_buys_equal_weekly_tranches():
    df = frame(np.full(120, 100.0))
    r = acc.simulate(df, 0, acc.normalise({"kind": "time", "tranches": 12}), FREE, 0.0, 100)
    assert r["deployed_4w"] == pytest.approx(4 / 12)   # decided on days 0, 7, 14, 21 and filled the next day
    assert r["deployed_12w"] == pytest.approx(1.0)


def test_ladder_buys_only_after_a_close_at_its_level_and_keeps_cash_otherwise():
    close = np.r_[[100.0] * 5, [89.0], [95.0] * 30]
    rule = acc.normalise({"kind": "ladder", "levels": [{"drop": 0.10, "weight": 0.5}, {"drop": 0.30, "weight": 0.5}]})
    r = acc.simulate(frame(close), 0, rule, FREE, 0.0, 30)
    assert r["deployed_4w"] == pytest.approx(0.5)       # the -30% level never closes, so half stays cash
    assert r["avg_price_ratio"] == pytest.approx(0.95)  # decided at the 89 close, filled at the next open (95)
    with_deadline = acc.normalise({"kind": "ladder", "levels": rule["levels"], "deadline_weeks": 2})
    assert acc.simulate(frame(close), 0, with_deadline, FREE, 0.0, 30)["deployed_4w"] == pytest.approx(1.0)


def test_hybrid_rally_trigger_and_deadline():
    rule = acc.normalise({"kind": "hybrid", "initial": 0.4, "levels": [{"drop": 0.2, "weight": 0.3}],
                          "rally_up": 0.15, "rally_tranches": 3, "deadline_weeks": 10})
    rally = np.r_[[100.0] * 10, [116.0] * 60]           # no dip; a +16% close on day 10 starts the rally tranches
    r = acc.simulate(frame(rally), 0, rule, FREE, 0.0, 60)
    assert r["deployed_4w"] == pytest.approx(1.0)       # 3 weekly tranches from day 10 finish by day 25
    flat = acc.simulate(frame(np.full(90, 100.0)), 0, rule, FREE, 0.0, 85)
    assert flat["deployed_4w"] == pytest.approx(0.4) and flat["deployed_12w"] == pytest.approx(1.0)  # deadline at week 10


def test_cash_yield_accrues_on_uninvested_cash():
    r = acc.simulate(frame(np.full(400, 100.0)), 0, acc.normalise({"kind": "ladder", "levels": [{"drop": 0.5, "weight": 1.0}]}),
                     FREE, 0.04, 365)
    assert r["value"] == pytest.approx(1.04, rel=1e-6)


def test_labels_need_a_deep_fall_and_a_new_cycle_low_for_A():
    # peak 100, cycle low 70, rebound to 95, then a 16% drop to 80: deep, but a higher low
    c = np.r_[np.linspace(50, 100, 30), np.linspace(95, 70, 10), np.linspace(75, 95, 10), [80.0] * 70]
    low = acc.cycle_low(c)
    assert low[49] == 70.0 and acc.label(c, low, 49) == "B"
    crash = np.r_[np.linspace(100, 60, 40), np.linspace(61, 75, 20), [50.0] * 70]
    low = acc.cycle_low(crash)
    assert low[59] == 60.0 and acc.label(crash, low, 59) == "A"
    assert acc.label(np.full(100, 10.0), acc.cycle_low(np.full(100, 10.0)), 0) == "C"


def test_no_lookahead():
    rng = np.random.default_rng(5)
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, 900)))
    df = frame(close, opens=np.r_[close[0], close[:-1]])
    rule = acc.normalise({"kind": "hybrid", "initial": 0.3, "levels": [{"drop": 0.1, "weight": 0.3}, {"above_low": 0.05, "weight": 0.2}],
                          "rally_up": 0.2, "deadline_weeks": 12})
    ref = acc.simulate(df, 100, rule, FREE, 0.0, 365)
    garbage = df.copy()
    garbage.iloc[466:] = rng.uniform(1, 1e6, size=garbage.iloc[466:].shape)   # everything after day 100 + 365
    assert acc.simulate(garbage, 100, rule, FREE, 0.0, 365) == ref
    assert acc.simulate(df.iloc[:466], 100, rule, FREE, 0.0, 365) == ref
    # what was bought by day t depends only on prices up to t: scramble everything after day t
    for t in (10, 40, 90):
        early = df.copy()
        early.iloc[100 + t + 1:] = rng.uniform(1, 1e6, size=early.iloc[100 + t + 1:].shape)
        assert acc.simulate(early, 100, rule, FREE, 0.0, t)["value"] == pytest.approx(
            acc.simulate(df, 100, rule, FREE, 0.0, t)["value"])


def test_rules_are_validated():
    with pytest.raises(SpecError):
        acc.normalise({"kind": "hybrid", "initial": 0.8, "levels": [{"drop": 0.1, "weight": 0.5}]})  # 130% of capital
    with pytest.raises(SpecError):
        acc.normalise({"kind": "ladder", "levels": [{"weight": 0.5}]})


def test_evaluate_reports_against_lump_and_records_a_trial_never_the_test_period(conn):
    from cryptoagent import store, trials
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, 3000)))
    source_id = store.get_or_create_source(conn, "bitstamp", "exchange")
    run_id = store.start_run(conn, source_id, {})
    mid = store.get_or_create_market(conn, source_id, "BTC", "USD", "BTC/USD", "btcusd")
    days = pd.date_range("2014-01-01", periods=len(close), freq="D")
    with conn:
        conn.executemany("INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                         " VALUES (?, '1d', ?, ?, ?, ?, ?, 1, ?)",
                         [(mid, d.strftime("%Y-%m-%dT00:00:00Z"), v, v, v, v, run_id) for d, v in zip(days, close)])
    out = trials.run_accumulation_trial(conn, "bitstamp:BTC/USD", {"kind": "lump"}, "train", "accum:test", "h", "claude")
    assert out["trial_id"] and out["overall"]["365d"]["vs_lump"]["median"] == pytest.approx(0.0)
    assert out["last_start"] <= "2020-12-31"   # 365-day windows end inside the train period
    with pytest.raises(SpecError):
        trials.run_accumulation_trial(conn, "bitstamp:BTC/USD", {"kind": "lump"}, "test", "accum:test", "h", "claude")
