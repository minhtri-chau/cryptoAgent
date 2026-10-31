"""The daily conversation starts from `digest`, so it must report exactly what happened since the
last briefing (not before, not invented), surface failed data runs and due calls, and the journal
that anchors it must be append-only."""

import sqlite3

import pandas as pd
import pytest

from cryptoagent import digest, predictions, store


@pytest.fixture
def btc(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    market_id = store.get_or_create_market(conn, source_id, "BTC", "USD", "BTC/USD", "BTC-USD")
    run_id = store.start_run(conn, source_id, {})

    def add(closes, start):
        with conn:
            for d, c in zip(pd.date_range(start, periods=len(closes), freq="D"), closes):
                conn.execute("INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume,"
                             " ingest_run_id) VALUES (?, '1d', ?, ?, ?, ?, ?, 1, ?)",
                             (market_id, d.strftime("%Y-%m-%dT00:00:00Z"), c, c, c, c, run_id))
    add.source_id = source_id
    return add


def test_digest_reports_resolutions_since_the_last_briefing_only(conn, btc):
    btc([100.0, 101.0], "2026-01-01")
    early = predictions.record(conn, "coinbase:BTC/USD", 1, 0.6, "judgment", "claude")
    btc([102.0], "2026-01-03")
    predictions.evaluate(conn)  # resolves `early`
    first = digest.digest(conn, since="2000-01-01")
    assert [p["id"] for p in first["resolved_since"]["predictions"]] == [early]
    with conn:  # a briefing written after the resolution (explicit time: same-second ties would be ambiguous)
        conn.execute("INSERT INTO journal (created_at, kind, author, summary) VALUES"
                     " ('2099-01-01T00:00:00Z', 'briefing', 'agent', 'day one')")
    later = digest.digest(conn)  # starts from that briefing: nothing resolved after it
    assert later["last_briefing"]["summary"] == "day one"
    assert later["resolved_since"]["predictions"] == []


def test_digest_surfaces_failed_runs_and_calls_due_soon(conn, btc):
    btc([100.0], "2026-01-01")
    run_id = store.start_run(conn, btc.source_id, {})
    store.finish_run(conn, run_id, 0, "NetworkError: getaddrinfo failed")
    predictions.record(conn, "coinbase:BTC/USD", 3, 0.5, "judgment", "claude")
    d = digest.digest(conn, since="2000-01-01", ahead_days=36500)
    runs = {(r["status"], r["error"]) for r in d["data"]["failed_runs_since"]}
    assert ("failed", "NetworkError: getaddrinfo failed") in runs and ("running", None) in runs  # killed runs show too
    assert len(d["due_soon"]["predictions"]) == 1 and d["open"]["predictions"] == 1


def test_journal_is_append_only(conn):
    entry = digest.add_entry(conn, "note", "user", "check ARB unlock next week")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE journal SET summary = 'x' WHERE id = ?", (entry,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM journal")
    with pytest.raises(sqlite3.IntegrityError):
        digest.add_entry(conn, "diary", "agent", "x")
