"""The daily report is read by the user each morning, so its warnings must be true (a source that
updated today is not "stale") and every scorecard row must say which market it is about."""

from datetime import datetime, timedelta, timezone

from cryptoagent import predictions, report, store


def test_defillama_snapshot_today_is_not_reported_stale(conn):
    """DefiLlama stores snapshots, not candles; a fresh snapshot must count as up to date."""
    source_id = store.get_or_create_source(conn, "defillama", "reference")
    run_id = store.start_run(conn, source_id, {})
    today = datetime.now(timezone.utc).date().isoformat()
    with conn:
        entity = conn.execute("INSERT INTO fundamental_entities (source_id, key, name, symbol) VALUES (?, 'aave',"
                              " 'Aave', 'AAVE')", (source_id,)).lastrowid
        conn.execute("INSERT INTO fundamental_obs (entity_id, metric, day, value, ingest_run_id)"
                     " VALUES (?, 'tvl', ?, 1e9, ?)", (entity, today, run_id))
    text = "\n".join(report._data_section(conn))
    assert f"| defillama | {today} |" in text
    assert "Stale" not in text


def test_old_defillama_snapshot_is_still_reported_stale(conn):
    source_id = store.get_or_create_source(conn, "defillama", "reference")
    run_id = store.start_run(conn, source_id, {})
    old = (datetime.now(timezone.utc).date() - timedelta(days=5)).isoformat()
    with conn:
        entity = conn.execute("INSERT INTO fundamental_entities (source_id, key, name, symbol) VALUES (?, 'aave',"
                              " 'Aave', 'AAVE')", (source_id,)).lastrowid
        conn.execute("INSERT INTO fundamental_obs (entity_id, metric, day, value, ingest_run_id)"
                     " VALUES (?, 'tvl', ?, 1e9, ?)", (entity, old, run_id))
    assert "**Stale:** defillama" in "\n".join(report._data_section(conn))


def test_scorecard_rows_name_their_market(conn):
    """Same model and horizon on two markets must be two distinguishable rows."""
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    run_id = store.start_run(conn, source_id, {})
    for symbol in ("BTC", "ETH"):
        market_id = store.get_or_create_market(conn, source_id, symbol, "USD", f"{symbol}/USD", f"{symbol}-USD")
        with conn:
            conn.execute("INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                         " VALUES (?, '1d', '2026-01-01T00:00:00Z', 1, 1, 1, 1, 1, ?)", (market_id, run_id))
        predictions.record(conn, f"coinbase:{symbol}/USD", 30, 0.6, "model", "base_rate_v1")
    rows = [line for line in report._scorecard_section(conn) if "base_rate_v1" in line]
    assert len(rows) == 2
    assert rows[0].startswith("| coinbase:BTC/USD |") and rows[1].startswith("| coinbase:ETH/USD |")
