"""Forward monitoring is the only clean evidence for an outside analyst's idea, because their
history is in-sample for them. So a monitor must record only what is knowable today (never a
signal from a day it wasn't watching), must not trust a handful of past cases, must link its
prediction as forward evidence, and must stop when the idea is retired. The conditional baseline
must never read the sealed test period."""

import json

import numpy as np
import pandas as pd
import pytest

from cryptoagent import hypotheses as hy, monitors, store
from cryptoagent.quant.patterns import SpecError


def add_candles(conn, closes, start="2019-01-01", symbol="BTC"):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    run_id = store.start_run(conn, source_id, {})
    market_id = store.get_or_create_market(conn, source_id, symbol, "USD", f"{symbol}/USD", f"{symbol}-USD")
    days = pd.date_range(start, periods=len(closes), freq="D")
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
            " VALUES (?, '1d', ?, ?, ?, ?, ?, 1, ?)",
            [(market_id, d.strftime("%Y-%m-%dT00:00:00Z"), c, c, c, c, run_id) for d, c in zip(days, closes)])
    return "coinbase:BTC/USD"


def register(conn):
    return hy.add_hypothesis(conn, slug="breakout-idea", kind="testable", subject="BTC", statement="20-day breakouts rise",
                             conditions="any", required_data="daily candles", methodology="forward returns",
                             created_by="agent")["hypothesis_id"]


def rising_with_breakouts(n=400):
    rng = np.random.default_rng(1)
    return 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n)))


def test_records_only_when_the_signal_is_on_the_latest_candle(conn):
    closes = rising_with_breakouts()
    closes[-1] = closes[:-1].max() * 1.10  # today closes above the prior 20-day high
    market = add_candles(conn, closes)
    hid = register(conn)
    monitors.add_monitor(conn, hid, market, "breakout_20", [], 30, "agent")
    first = monitors.run(conn)[0]
    assert first["active"] and first["prediction_id"]
    assert monitors.run(conn)[0].get("prediction_id") is None  # same candle: nothing recorded twice
    evidence = hy.show(conn, hid)["evidence"]
    assert evidence[-1]["basis"] == "forward" and evidence[-1]["cite_ref"] == str(first["prediction_id"])


def test_no_back_recording_of_signals_from_earlier_days(conn):
    closes = rising_with_breakouts()
    closes[-2] = closes[:-2].max() * 1.10  # signal yesterday
    closes[-1] = closes[-2] * 0.97          # nothing today
    market = add_candles(conn, closes)
    hid = register(conn)
    monitors.add_monitor(conn, hid, market, "breakout_20", [], 30, "agent")
    assert monitors.run(conn) == [{"monitor_id": 1, "hypothesis_id": hid, "market": market,
                                   "as_of": "2020-02-04", "horizon_days": 30, "active": False}]


def test_few_past_cases_stay_near_the_base_rate(conn):
    closes = rising_with_breakouts()
    closes[-1] = closes[:-1].max() * 1.10
    market = add_candles(conn, closes)
    hid = register(conn)
    monitors.add_monitor(conn, hid, market, "breakout_20", [], 30, "agent")
    row = monitors.run(conn)[0]
    ev = conn.execute("SELECT evidence FROM predictions WHERE id = ?", (row["prediction_id"],)).fetchone()[0]

    e = json.loads(ev)
    k, n, b, m = e["past_up"], e["past_signal_windows"], e["base_share_up"], monitors.PRIOR_WEIGHT
    assert row["prob_up"] == pytest.approx((k + m * b) / (n + m), abs=1e-3)
    assert abs(row["prob_up"] - b) <= abs(k / n - b) if n else row["prob_up"] == pytest.approx(b, abs=1e-3)


def test_retired_hypotheses_stop_and_horizons_must_pair_with_the_base_rate(conn):
    closes = rising_with_breakouts()
    closes[-1] = closes[:-1].max() * 1.10
    market = add_candles(conn, closes)
    hid = register(conn)
    with pytest.raises(hy.RegistryError, match="horizon"):
        monitors.add_monitor(conn, hid, market, "breakout_20", [], 56, "agent")
    monitors.add_monitor(conn, hid, market, "breakout_20", [], 30, "agent")
    hy.set_status(conn, hid, "retired", "agent", note="test")
    assert monitors.run(conn) == []
    with pytest.raises(hy.RegistryError, match="retired"):
        monitors.add_monitor(conn, hid, market, "breakout_20", [], 7, "agent")


def test_conditional_baseline_never_reads_the_test_period(conn):
    market = add_candles(conn, rising_with_breakouts(1700), start="2018-01-01")  # runs past the train end
    with pytest.raises(SpecError, match="sealed"):
        monitors.conditional_baseline(conn, market, 30, [], "test")
    everything = monitors.conditional_baseline(conn, market, 30, [], "train")
    assert everything["matching_days"] == everything["all_days"]
    up = monitors.conditional_baseline(conn, market, 30, [{"field": "close", "op": ">", "value": 150.0}], "train")
    assert up["matching_days"]["days"] < up["all_days"]["days"]
    last_day = pd.Timestamp("2021-12-31") - pd.Timedelta(days=30)
    assert up["all_days"]["days"] == len(pd.date_range("2018-01-01", last_day))  # windows end by the period end
