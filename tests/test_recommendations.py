"""Recommendations are what the user would act on, so the ledger must keep the agent honest:
no back-dating, no buy without a stop, outcomes computed mechanically by the same engine as
backtests (with costs), every action compared with simply holding the market, and no edits."""

import sqlite3

import pandas as pd
import pytest

from cryptoagent import recommendations as rec
from cryptoagent import store
from cryptoagent.predictions import LedgerError


@pytest.fixture
def add(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    market_id = store.get_or_create_market(conn, source_id, "ETH", "USD", "ETH/USD", "ETH-USD")
    run_id = store.start_run(conn, source_id, {})

    def add_bars(rows, start):
        """rows of (open, high, low, close)."""
        with conn:
            for day, (o, h, l, c) in zip(pd.date_range(start, periods=len(rows), freq="D"), rows):
                conn.execute("INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume,"
                             " ingest_run_id) VALUES (?, '1d', ?, ?, ?, ?, ?, 1, ?)",
                             (market_id, day.strftime("%Y-%m-%dT00:00:00Z"), o, h, l, c, run_id))
    add_bars([(100, 101, 99, 100)] * 20, "2024-01-01")  # ATR14 = 2 on 2024-01-20
    add_bars.market_id = market_id
    return add_bars


TEXT = dict(entry="breakout_20 at close", invalidation="close below SMA50", risks="r", limitations="l",
            created_by="claude")


def test_buy_needs_a_stop_and_binds_to_latest_candle(conn, add):
    with pytest.raises(LedgerError, match="stop"):
        rec.record(conn, "coinbase:ETH/USD", 5, "buy", **TEXT)
    out = rec.record(conn, "coinbase:ETH/USD", 5, "buy", stop_atr=2, target_atr=3, **TEXT)
    assert (out["as_of"], out["target_day"], out["atr14"], out["stop_level"]) == ("2024-01-20", "2024-01-25", 2.0, 96.0)
    with pytest.raises(sqlite3.IntegrityError, match="latest stored candle"):
        conn.execute("INSERT INTO recommendations (created_by, market_id, as_of, as_of_close, horizon_days, target_day,"
                     " action, entry, invalidation, risks, limitations) VALUES ('x', ?, '2024-01-10', 100, 5,"
                     " '2024-01-15', 'no_trade', 'e', 'i', 'r', 'l')", (add.market_id,))


def test_buy_is_replayed_with_stop_and_costs(conn, add):
    rid = rec.record(conn, "coinbase:ETH/USD", 3, "buy", stop_atr=2, **TEXT)["id"]
    add([(100, 101, 99, 100)], "2024-01-21")
    assert rec.evaluate(conn)["waiting"] == 1  # target day 2024-01-23 not stored yet
    add([(99, 100, 95, 97), (97, 98, 96, 98)], "2024-01-22")  # stop = 100 - 2 x 2 = 96 hit on the 22nd
    assert rec.evaluate(conn)["resolved"] == 1
    o = conn.execute("SELECT * FROM recommendation_outcomes WHERE recommendation_id = ?", (rid,)).fetchone()
    expected = (96 * 0.998 * 0.994) / (100 * 1.002 * 1.006) - 1  # default costs: 0.6% fee, 0.2% slippage per side
    assert (o["exit_reason"], o["entry_price"], o["exit_price"]) == ("stop", 100.0, 96.0)
    assert o["trade_return"] == pytest.approx(expected)
    assert o["market_return"] == pytest.approx(98 / 100 - 1)


def test_no_trade_is_scored_against_the_market_and_ledger_is_append_only(conn, add):
    rid = rec.record(conn, "coinbase:ETH/USD", 1, "no_trade", **TEXT)["id"]
    add([(100, 112, 99, 110)], "2024-01-21")
    rec.evaluate(conn)
    o = conn.execute("SELECT market_return, trade_return FROM recommendation_outcomes").fetchone()
    assert o["market_return"] == pytest.approx(0.10) and o["trade_return"] is None  # the opportunity cost of waiting
    scores = {s["action"]: s for s in rec.score(conn)}
    assert scores["no_trade"]["resolved"] == 1 and "too few" in scores["no_trade"]["warning"]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE recommendations SET action = 'buy' WHERE id = ?", (rid,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM recommendation_outcomes")


def test_missing_horizon_candle_becomes_unresolvable_after_grace(conn, add):
    rec.record(conn, "coinbase:ETH/USD", 1, "wait", **TEXT)
    add([(100, 101, 99, 100)] * 8, "2024-01-22")  # 2024-01-21 never stored; latest = target + 8
    assert rec.evaluate(conn)["unresolvable"] == 1
