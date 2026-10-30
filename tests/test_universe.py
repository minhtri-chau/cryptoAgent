"""The scanner decides which coins the researcher even looks at. If its universe used future
information, or only coins that survived to today, every historical scan would look smarter
than it could have been. So: trailing data only, delisted coins included while they traded."""

import numpy as np
import pandas as pd
import pytest

from cryptoagent import store
from cryptoagent.quant import universe


@pytest.fixture
def add(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    run_id = store.start_run(conn, source_id, {})

    def add_candles(symbol, closes, start="2023-01-01", volume=1_000_000.0):
        market_id = store.get_or_create_market(conn, source_id, symbol.split("/")[0], "USD", symbol, symbol)
        days = pd.date_range(start, periods=len(closes), freq="D")
        with conn:
            conn.executemany(
                "INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                " VALUES (?, '1d', ?, ?, ?, ?, ?, ?, ?)",
                [(market_id, d.strftime("%Y-%m-%dT00:00:00Z"), c, c * 1.01, c * 0.99, c, volume, run_id)
                 for d, c in zip(days, closes)])
    return add_candles


def markets(result):
    return [m["market"] for m in result["markets"]]


def test_membership_rules():
    days = pd.date_range("2024-01-01", periods=250, freq="D")
    df = pd.DataFrame({"close": 2.0, "volume": 600_000.0}, index=days)  # $1.2M a day
    as_of = days[-1]
    assert universe.membership(df, as_of)[:2] == (True, "member")
    assert universe.membership(df, as_of + pd.Timedelta(days=4))[1] == "stale"
    assert universe.membership(df.iloc[-150:], as_of)[1] == "short_history"
    assert universe.membership(df.assign(volume=400_000.0), as_of)[1] == "illiquid"  # $0.8M a day


def test_coin_delisted_later_is_in_past_scans_only(conn, add):
    add("BTC/USD", np.linspace(100, 200, 700))
    add("DEAD/USD", np.linspace(10, 1, 400))  # last candle 2024-02-04, then delisted
    assert "coinbase:DEAD/USD" in markets(universe.scan(conn, as_of="2024-02-01"))
    after = universe.scan(conn, as_of="2024-03-01")
    assert "coinbase:DEAD/USD" not in markets(after) and after["counts"]["stale"] == 1


def test_scan_uses_only_data_up_to_as_of(conn, add):
    rng = np.random.default_rng(3)
    add("BTC/USD", 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 500))))
    add("ALT/USD", 10 * np.exp(np.cumsum(rng.normal(0, 0.04, 500))))
    reference = universe.scan(conn, as_of="2024-01-31")
    add("BTC/USD", rng.uniform(1, 1e6, 100), start="2024-05-16")  # garbage after the as-of day
    add("ALT/USD", rng.uniform(1, 1e6, 100), start="2024-05-16")
    assert universe.scan(conn, as_of="2024-01-31") == reference


def test_relative_strength_against_btc(conn, add):
    add("BTC/USD", np.r_[np.full(300, 100.0), np.full(90, 110.0)])  # +10% over 90 days
    add("ALT/USD", np.r_[np.full(300, 10.0), np.full(90, 13.2)])    # +32%
    alt = next(m for m in universe.scan(conn)["markets"] if m["market"] == "coinbase:ALT/USD")
    assert alt["rs90"] == pytest.approx(1.32 / 1.10 - 1)
