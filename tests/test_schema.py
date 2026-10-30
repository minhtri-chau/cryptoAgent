"""The schema is the last line of defence: impossible candles must not be storable, and the
listing history that point-in-time universes are rebuilt from must not be rewritable."""

import sqlite3

import pytest

from cryptoagent import db, store


def test_migrate_is_idempotent(conn):
    assert db.migrate(conn) == []
    assert db.status(conn)["pending"] == []


@pytest.fixture
def market(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    market_id = store.get_or_create_market(conn, source_id, "BTC", "USD", "BTC/USD", "BTC-USD")
    run_id = store.start_run(conn, source_id, {})
    return market_id, run_id


@pytest.mark.parametrize("o,h,l,c,v", [
    (100, 99, 98, 100, 1),     # high below close
    (100, 110, 101, 105, 1),   # low above open
    (0, 110, 90, 100, 1),      # zero price
    (100, 110, 90, 100, -1),   # negative volume
])
def test_impossible_candles_are_rejected(conn, market, o, h, l, c, v):
    market_id, run_id = market
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                     " VALUES (?, '1d', '2024-01-01T00:00:00Z', ?, ?, ?, ?, ?, ?)", (market_id, o, h, l, c, v, run_id))


def test_status_history_is_append_only(conn, market):
    market_id, run_id = market
    store.record_status(conn, market_id, "online", True, run_id)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE market_status_history SET status = 'delisted'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM market_status_history")


def test_status_is_recorded_only_on_change(conn, market):
    market_id, run_id = market
    assert store.record_status(conn, market_id, "online", True, run_id)
    assert not store.record_status(conn, market_id, "online", True, run_id)
    assert store.record_status(conn, market_id, "delisted", False, run_id)
    assert [r[0] for r in conn.execute("SELECT status FROM market_status_history ORDER BY id")] == ["online", "delisted"]
