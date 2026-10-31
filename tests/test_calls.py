"""Scoring commentators is only fair and only useful if each call is judged from the price at the
moment it was published (not a better one), resolved by the market rather than by interpretation,
compared with what would have happened anyway, and kept apart when it was found after the fact."""

import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

from cryptoagent import calls, store
from cryptoagent.predictions import LedgerError


@pytest.fixture
def btc(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    store.get_or_create_market(conn, source_id, "BTC", "USD", "BTC/USD", "BTC-USD")
    run_id = store.start_run(conn, source_id, {})
    market_id = conn.execute("SELECT id FROM markets").fetchone()[0]

    def add(closes, start, spread=0.01):
        with conn:
            conn.executemany(
                "INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                " VALUES (?, '1d', ?, ?, ?, ?, ?, 1, ?)",
                [(market_id, d.strftime("%Y-%m-%dT00:00:00Z"), c, c * (1 + spread), c * (1 - spread), c, run_id)
                 for d, c in zip(pd.date_range(start, periods=len(closes), freq="D"), closes)])
    return add


def call(conn, **kw):
    base = dict(creator="Chart Guy", source_url="https://youtu.be/x?t=60", published_on="2026-01-10",
                market="coinbase:BTC/USD", call_type="up", deadline="2026-01-20", recorded_by="agent",
                today=date(2026, 1, 12))
    return calls.record(conn, **(base | kw))


def test_anchored_to_the_publication_day_close(conn, btc):
    btc(np.linspace(100, 109, 10), "2026-01-01")  # 2026-01-10 closes at 109
    c = call(conn)
    assert (c["published_close"], c["timing"]) == (109.0, "forward")
    with pytest.raises(LedgerError, match="no stored"):
        call(conn, published_on="2026-02-01", deadline="2026-03-01")
    with pytest.raises(sqlite3.IntegrityError, match="anchored"):
        conn.execute("INSERT INTO external_calls (recorded_by, creator, source_url, published_on, market_id,"
                     " published_close, call_type, deadline, timing) VALUES ('agent', 'x', 'https://a', '2026-01-10',"
                     " 1, 50, 'up', '2026-01-20', 'forward')")  # a nicer price than the real close


def test_calls_resolve_from_the_market_and_targets_resolve_early(conn, btc):
    btc(np.linspace(100, 109, 10), "2026-01-01")
    up = call(conn)["id"]
    target = call(conn, call_type="reach_above", target=120.0, deadline="2026-03-01")["id"]
    miss = call(conn, call_type="reach_below", target=90.0, deadline="2026-01-15")["id"]
    btc([110.0, 115.0, 121.0, 118.0, 112.0, 111.0, 110.0, 108.0, 107.0, 106.0], "2026-01-11")  # through 01-20
    assert calls.resolve(conn, today=date(2026, 1, 21)) == {"resolved": 3, "unresolvable": 0, "waiting": 0}
    hits = dict(conn.execute("SELECT call_id, hit FROM external_outcomes").fetchall())
    assert hits[target] == 1  # touched 121 * 1.01 high on 01-13, long before its deadline
    assert hits[miss] == 0    # never fell to 90 by 01-15
    assert hits[up] == 0      # 106 on 01-20 < 109 at publication


def test_base_rate_uses_only_history_before_publication():
    days = pd.date_range("2025-01-01", periods=200, freq="D")
    close = np.r_[np.full(100, 100.0), np.linspace(100, 200, 100)]
    df = pd.DataFrame({"close": close, "high": close, "low": close}, index=days)
    assert calls.base_rate(df.iloc[:100], "up", 10) == 0.0  # flat history: never up
    assert calls.base_rate(df, "up", 10) > 0.4               # later history must not leak into an early call


def test_historical_calls_are_scored_apart_and_flagged(conn, btc):
    btc(np.linspace(100, 130, 31), "2026-01-01")
    call(conn, today=date(2026, 6, 1))  # found months later
    calls.resolve(conn, today=date(2026, 6, 1))
    s = calls.score(conn)[0]
    assert (s["timing"], s["resolved"]) == ("historical", 1) and "flatter" in s["caution"]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM external_calls")


def test_quote_must_stay_short():
    with pytest.raises(LedgerError, match="25 words"):
        calls.record(None, creator="x", source_url="https://a", published_on="2026-01-10", market="m",
                     call_type="up", deadline="2026-01-20", recorded_by="agent", quote=" ".join(["w"] * 26))
