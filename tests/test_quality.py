"""Quality checks must surface each kind of bad data that would silently distort indicators
or backtests later (gaps, implausible jumps, closes that disagree with an independent
reference), without altering the data and without duplicating findings on re-runs."""

import pytest

from cryptoagent import quality, store


@pytest.fixture
def btc(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    market_id = store.get_or_create_market(conn, source_id, "BTC", "USD", "BTC/USD", "BTC-USD")
    run_id = store.start_run(conn, source_id, {})
    return market_id, run_id


def add(conn, market_id, run_id, day, close):
    conn.execute("INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                 " VALUES (?, '1d', ?, ?, ?, ?, ?, 1, ?)",
                 (market_id, f"{day}T00:00:00Z", close, close, close, close, run_id))


def test_gap_is_one_issue_at_first_missing_day(conn, btc):
    for day, close in [("2024-01-01", 100), ("2024-01-02", 101), ("2024-01-06", 102)]:
        add(conn, *btc, day, close)
    assert quality.run_checks(conn)["missing_candle"] == 1
    ts, detail = conn.execute("SELECT ts, detail FROM quality_issues").fetchone()
    assert ts == "2024-01-03T00:00:00Z" and '"missing_days": 3' in detail


def test_outlier_only_between_adjacent_days(conn, btc):
    for day, close in [("2024-01-01", 100), ("2024-01-02", 160), ("2024-01-03", 150), ("2024-01-10", 400)]:
        add(conn, *btc, day, close)
    assert quality.run_checks(conn)["outlier_return"] == 1  # +60% flagged; the jump across the gap is not
    assert conn.execute("SELECT ts FROM quality_issues WHERE check_name = 'outlier_return'").fetchone()[0] \
        == "2024-01-02T00:00:00Z"


def test_reference_mismatch_and_rerun_adds_nothing(conn, btc):
    market_id, run_id = btc
    ref_source = store.get_or_create_source(conn, "coinmetrics", "reference")
    asset_id = store.get_or_create_asset(conn, "BTC")
    for day, close, ref in [("2024-01-01", 100, 101), ("2024-01-02", 100, 110)]:
        add(conn, market_id, run_id, day, close)
        conn.execute("INSERT INTO reference_prices (asset_id, source_id, metric, day, value, ingest_run_id)"
                     " VALUES (?, ?, 'PriceUSD', ?, ?, ?)", (asset_id, ref_source, day, ref, run_id))
    assert quality.run_checks(conn)["reference_mismatch"] == 1  # 1% is within tolerance, ~9% is not
    assert quality.run_checks(conn) == {"missing_candle": 0, "outlier_return": 0, "reference_mismatch": 0}
    summary = quality.summary(conn)
    assert summary["totals"] == {"reference_mismatch": 1}
    assert conn.execute("SELECT count(*) FROM candles").fetchone()[0] == 2  # data is never altered
