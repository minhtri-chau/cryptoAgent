"""The promotion loop is the only path by which an idea becomes something the user might act on.
It must be impossible to shortcut: no skipping stages, no approval or test authorisation except
by the user, gates applied exactly as written, the sealed test spent at most once and only with
permission, and a paper record that cannot be back-dated, duplicated or edited."""

import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from cryptoagent import store, strategies, trials
from cryptoagent.strategies import StrategyError

SPEC = {"market": "coinbase:BTC/USD", "pattern": "breakout_20", "conditions": [], "exit": {"hold_days": 5}}


@pytest.fixture
def btc(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    market_id = store.get_or_create_market(conn, source_id, "BTC", "USD", "BTC/USD", "BTC-USD")
    run_id = store.start_run(conn, source_id, {})

    def add(closes, start):
        with conn:
            conn.executemany(
                "INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                " VALUES (?, '1d', ?, ?, ?, ?, ?, 1, ?)",
                [(market_id, d.strftime("%Y-%m-%dT00:00:00Z"), c, c * 1.01, c * 0.99, c, run_id)
                 for d, c in zip(pd.date_range(start, periods=len(closes), freq="D"), closes)])
    add.market_id = market_id
    return add


def version_id(conn, name="trend"):
    return conn.execute("SELECT v.id FROM strategy_versions v JOIN strategies s ON s.id = v.strategy_id"
                        " WHERE s.name = ? ORDER BY v.version DESC", (name,)).fetchone()[0]


def force(conn, vid, *events):
    """Walk a version through allowed stages (as the system or the user would)."""
    with conn:
        for event, by in events:
            conn.execute("INSERT INTO strategy_events (strategy_version_id, event, decided_by) VALUES (?, ?, ?)",
                         (vid, event, by))


def fake_trial(conn, spec_hash, period, results, family="f"):
    with conn:
        conn.execute("INSERT INTO trials (created_by, family, hypothesis, spec, spec_hash, market, period, data_through,"
                     " costs, trades, mean_return, results) VALUES ('t', ?, 'h', '{}', ?, 'm', ?, '2024-06-30', '{}',"
                     " ?, ?, ?)", (family, spec_hash, period, results.get("trades", 0), results.get("mean_return"),
                                    json.dumps(results)))


GOOD = {"trades": 40, "mean_return": 0.03, "mean_return_ci90": [0.01, 0.05],
        "cost_sensitivity": {"double_costs": {"mean_return": 0.02}},
        "forward_returns": {"after_signal": {"mean": 0.04}, "all_days": {"mean": 0.01}},
        "windows": {"summary": {"share_positive_mean": 0.6}}}


@pytest.mark.parametrize("change,failed", [
    ({"trades": 29}, "trades >= 30"),
    ({"mean_return_ci90": [-0.01, 0.05]}, "CI90 lower bound > 0"),
    ({"cost_sensitivity": {"double_costs": {"mean_return": -0.001}}}, "mean > 0 at double costs"),
    ({"forward_returns": {"after_signal": {"mean": 0.01}, "all_days": {"mean": 0.01}}},
     "forward return after signal > all days"),
    ({"windows": {"summary": {"share_positive_mean": 0.4}}}, "positive mean in >= 50% of years with trades"),
])
def test_validation_gate_fails_on_each_condition_alone(change, failed):
    assert strategies.validation_gate(GOOD)["passed"]
    gate = strategies.validation_gate(GOOD | change)
    assert not gate["passed"] and [k for k, ok in gate["checks"].items() if not ok] == [failed]


def test_stages_cannot_be_skipped_and_decisions_are_user_only(conn):
    strategies.propose(conn, "trend", SPEC, "trial 1 and 2", "agent")
    vid = version_id(conn)
    with pytest.raises(sqlite3.IntegrityError, match="not allowed"):
        force(conn, vid, ("approved", "user"))  # straight from proposed to approved
    force(conn, vid, ("validation_passed", "system"))
    with pytest.raises(sqlite3.IntegrityError, match="only the user"):
        strategies.authorize_test(conn, "trend", "agent")
    with pytest.raises(sqlite3.IntegrityError, match="only the user"):
        strategies.decide(conn, "trend", "rejected", "system")
    with pytest.raises(sqlite3.IntegrityError, match="only through a migration"):
        conn.execute("INSERT INTO strategy_transitions VALUES ('proposed', 'approved')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM strategy_events")


def test_check_uses_trials_of_the_same_spec(conn):
    out = strategies.propose(conn, "trend", SPEC, "r", "agent")
    with pytest.raises(StrategyError, match="no validation trial"):
        strategies.check(conn, "trend")
    fake_trial(conn, out["spec_hash"], "train", {"mean_return": 0.02})
    fake_trial(conn, "other-spec", "validation", GOOD)  # a different spec's success does not count
    with pytest.raises(StrategyError, match="no validation trial"):
        strategies.check(conn, "trend")
    fake_trial(conn, out["spec_hash"], "validation", GOOD)
    result = strategies.check(conn, "trend")
    assert result["stage"] == "validation_passed" and result["trials_total"] == 3


def test_a_losing_train_run_fails_the_gate_even_if_validation_looks_good(conn):
    out = strategies.propose(conn, "trend", SPEC, "r", "agent")
    fake_trial(conn, out["spec_hash"], "train", {"mean_return": -0.01})
    fake_trial(conn, out["spec_hash"], "validation", GOOD)
    result = strategies.check(conn, "trend")
    assert result["stage"] == "validation_failed"
    assert [k for k, ok in result["checks"].items() if not ok] == ["train trial exists with mean > 0"]


def test_sealed_test_needs_user_authorisation_and_runs_once(conn, btc):
    btc(100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0.001, 0.03, 2300))), "2019-01-01")
    out = strategies.propose(conn, "trend", SPEC, "r", "agent")
    fake_trial(conn, out["spec_hash"], "validation", GOOD)
    force(conn, version_id(conn), ("validation_passed", "system"))
    with pytest.raises(StrategyError, match="authorisation"):
        strategies.run_test(conn, "trend", "claude")
    strategies.authorize_test(conn, "trend", "user", "go")
    result = strategies.run_test(conn, "trend", "claude")
    assert result["stage"] in ("paper", "test_failed") and result["test_trial"]
    assert conn.execute("SELECT count(*) FROM trials WHERE spec_hash = ? AND period = 'test'",
                        (out["spec_hash"],)).fetchone()[0] == 1
    with pytest.raises(StrategyError):
        strategies.run_test(conn, "trend", "claude")


def test_paper_trades_are_bound_resolved_by_replay_and_never_duplicated(conn, btc):
    closes = np.r_[np.full(40, 100.0), [110.0]]  # day 40 closes above the prior 20-day high: breakout_20
    btc(closes, "2026-01-01")
    strategies.propose(conn, "trend", SPEC, "r", "agent")
    vid = version_id(conn)
    force(conn, vid, ("validation_passed", "system"), ("test_authorized", "user"), ("test_passed", "system"),
          ("paper", "system"))
    assert strategies.paper_step(conn)["signals"] == 1
    assert strategies.paper_step(conn)["signals"] == 0  # same day again: nothing new
    trade = conn.execute("SELECT as_of, as_of_close FROM paper_trades").fetchone()
    assert tuple(trade) == ("2026-02-10", 110.0)
    with pytest.raises(sqlite3.IntegrityError, match="latest stored candle"):
        conn.execute("INSERT INTO paper_trades (strategy_version_id, market_id, as_of, as_of_close)"
                     " VALUES (?, ?, '2026-02-01', 100)", (vid, btc.market_id))
    btc([115.0], "2026-02-11")  # a fresh 20-day breakout (above the 111.1 high), but the first trade is open
    assert strategies.paper_step(conn)["signals"] == 0
    btc([116.0, 117.0, 118.0, 119.0], "2026-02-12")  # horizon (5 days) completes on 2026-02-15
    assert strategies.paper_step(conn)["resolved"] == 1
    o = conn.execute("SELECT exit_reason, entry_price, exit_price, trade_return FROM paper_outcomes").fetchone()
    assert (o["exit_reason"], o["entry_price"], o["exit_price"]) == ("time", 115.0, 119.0)
    assert o["trade_return"] == pytest.approx((119 * 0.998 * 0.994) / (115 * 1.002 * 1.006) - 1)
    gate = strategies.paper_gate(conn, vid)
    assert gate["resolved_trades"] == 1 and not gate["passed"]  # far from 90 days and 10 trades


def test_only_approved_strategies_produce_signals_for_the_user(conn, btc):
    btc(np.r_[np.full(40, 100.0), [110.0]], "2026-01-01")
    strategies.propose(conn, "trend", SPEC, "r", "agent")
    vid = version_id(conn)
    force(conn, vid, ("validation_passed", "system"), ("test_authorized", "user"), ("test_passed", "system"),
          ("paper", "system"))
    strategies.paper_step(conn)
    assert strategies.approved_signals(conn) == []  # still in paper
    force(conn, vid, ("paper_review", "system"))
    strategies.decide(conn, "trend", "approved", "user", "looks good")
    signals = strategies.approved_signals(conn)
    assert [(s["market"], s["as_of"]) for s in signals] == [("coinbase:BTC/USD", "2026-02-10")]
