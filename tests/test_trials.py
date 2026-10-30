"""The trial registry is the defence against overfitting: every run is on record, a run
never sees prices after its period, and the test period gives each specification exactly
one look, after validation, enforced by the database rather than by good intentions."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from cryptoagent import store, trials
from cryptoagent.cli import parse_when
from cryptoagent.quant.patterns import SpecError


@pytest.fixture
def markets(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    run_id = store.start_run(conn, source_id, {})
    rng = np.random.default_rng(11)
    days = pd.date_range("2019-01-01", "2025-06-30", freq="D")
    for symbol in ("BTC/USD", "ETH/USD"):
        market_id = store.get_or_create_market(conn, source_id, symbol.split("/")[0], "USD", symbol, symbol)
        close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, len(days))))
        with conn:
            conn.executemany(
                "INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                " VALUES (?, '1d', ?, ?, ?, ?, ?, 1, ?)",
                [(market_id, d.strftime("%Y-%m-%dT00:00:00Z"), c, c * 1.02, c * 0.98, c, run_id)
                 for d, c in zip(days, close)])
    return conn


SPEC = {"market": "coinbase:ETH/USD", "pattern": "breakout_20",
        "conditions": [{"market": "coinbase:BTC/USD", "field": "sma50_change20", "op": ">", "value": 0}],
        "exit": {"hold_days": 10, "stop_atr": 2}}


def run(conn, period, spec=SPEC):
    return trials.run_trial(conn, spec, period, "eth-breakout-btc-slope", "test hypothesis", "test")


def test_validation_never_uses_prices_after_its_period(markets):
    out = run(markets, "validation")
    assert out["period_range"] == ["2022-01-01", "2024-06-30"]
    full = trials.run_trial(markets, SPEC, "validation", "f", "h", "t", include_trades=True)
    assert full["trade_list"] and max(t["exit_day"] for t in full["trade_list"]) <= "2024-06-30"
    assert min(t["signal_day"] for t in full["trade_list"]) >= "2022-01-01"


def test_condition_filters_signals(markets):
    without = dict(SPEC, conditions=[])
    filtered, unfiltered = run(markets, "train"), run(markets, "train", without)
    assert 0 < filtered["forward_returns"]["after_signal"]["n"] < unfiltered["forward_returns"]["after_signal"]["n"]


def test_test_period_is_sealed(markets):
    with pytest.raises(sqlite3.IntegrityError, match="validation trial"):
        run(markets, "test")
    run(markets, "validation")
    assert run(markets, "test")["period"] == "test"
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        run(markets, "test")
    # Tweaking costs is not a new specification: still sealed.
    with pytest.raises(sqlite3.IntegrityError):
        trials.run_trial(markets, SPEC, "test", "f", "h", "t", trials.Costs(fee=0.0))


def test_every_run_is_recorded_and_counted(markets):
    run(markets, "train")
    second = run(markets, "train")
    assert (second["trials_total"], second["trials_in_family"], second["trials_of_this_spec"]) == (2, 2, 2)
    assert second["warning_costs"]
    with pytest.raises(sqlite3.IntegrityError):
        markets.execute("DELETE FROM trials")
    with pytest.raises(sqlite3.IntegrityError):
        markets.execute("UPDATE trials SET mean_return = 1")


def test_equivalent_specs_hash_the_same_and_bad_specs_are_refused():
    _, a = trials.canonical(dict(SPEC, exit={"hold_days": 10, "stop_atr": 2}))
    _, b = trials.canonical(dict(SPEC, exit={"stop_atr": 2.0, "hold_days": 10.0}))
    assert a == b
    with pytest.raises(SpecError, match="unknown pattern"):
        trials.canonical(dict(SPEC, pattern="cup_and_handle"))
    with pytest.raises(SpecError, match="exit"):
        trials.canonical(dict(SPEC, exit={"stop_atr": 2}))


def test_parse_when():
    assert parse_when("bitstamp:BTC/USD sma50_change20 > 0") == \
        {"market": "bitstamp:BTC/USD", "field": "sma50_change20", "op": ">", "value": 0.0}
    assert parse_when("trend == uptrend") == {"field": "trend", "op": "==", "value": "uptrend"}


def test_show_returns_every_recorded_number(markets):
    """Reviewers must be able to trace forward returns and cost sensitivity, not just the summary."""
    out = run(markets, "train")
    shown = trials.show(markets, out["trial_id"])
    assert shown["results"]["forward_returns"] == out["forward_returns"]
    assert shown["results"]["cost_sensitivity"] == out["cost_sensitivity"]
    with pytest.raises(SpecError):
        trials.show(markets, 999)
