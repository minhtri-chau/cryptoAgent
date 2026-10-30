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
    v1 = "universe-v1"  # flat prices: v2 would exclude them as pegged-like (tested below)
    assert universe.membership(df, as_of, version=v1)[:2] == (True, "member")
    assert universe.membership(df, as_of + pd.Timedelta(days=4), version=v1)[1] == "stale"
    assert universe.membership(df.iloc[-150:], as_of, version=v1)[1] == "short_history"
    assert universe.membership(df.assign(volume=400_000.0), as_of, version=v1)[1] == "illiquid"  # $0.8M a day


def test_coin_delisted_later_is_in_past_scans_only(conn, add):
    add("BTC/USD", np.linspace(100, 200, 700))
    add("DEAD/USD", np.linspace(10, 1, 400))  # last candle 2024-02-04, then delisted
    assert "coinbase:DEAD/USD" in markets(universe.scan(conn, as_of="2024-02-01", version="universe-v1"))
    after = universe.scan(conn, as_of="2024-03-01", version="universe-v1")
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
    alt = next(m for m in universe.scan(conn, version="universe-v1")["markets"] if m["market"] == "coinbase:ALT/USD")
    assert alt["rs90"] == pytest.approx(1.32 / 1.10 - 1)


def volatile(n, sigma, seed, start=10.0):
    return start * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, sigma, n)))


def test_v2_excludes_listed_pegged_and_wrapped_assets(conn, add):
    """Stablecoins, gold tokens and wrapped BTC/ETH are not crypto opportunities; PAXG and
    wrapped tokens move like their underlying, so only a list catches them."""
    add("BTC/USD", volatile(400, 0.03, 1, 100))
    add("PAXG/USD", volatile(400, 0.008, 2, 2000))  # gold-like: ~15% a year, above the backstop
    add("WBTC/USD", volatile(400, 0.03, 1, 100))    # moves exactly like BTC
    add("ALT/USD", volatile(400, 0.05, 3))
    result = universe.scan(conn)
    assert sorted(markets(result)) == ["coinbase:ALT/USD", "coinbase:BTC/USD"]
    assert result["counts"]["pegged_or_wrapped"] == 2
    assert {e["market"]: e["excluded_category"] for e in result["excluded"]} == \
        {"coinbase:PAXG/USD": "commodity_token", "coinbase:WBTC/USD": "wrapped_or_staked"}
    assert "coinbase:PAXG/USD" in markets(universe.scan(conn, version="universe-v1"))  # v1 reproducible


def test_v2_backstop_catches_unlisted_stablecoins_with_trailing_data_only(conn, add):
    """A new stablecoin not yet on the list is excluded by its low volatility, measured only on
    data up to the scan date: a coin that later loses its peg is still excluded before that."""
    add("BTC/USD", volatile(500, 0.03, 4, 100))
    add("NEWUSD/USD", np.r_[1 + np.random.default_rng(5).normal(0, 0.0005, 400), volatile(100, 0.08, 6, 1.0)],
        volume=5_000_000.0)  # well above the liquidity line, so only the peg rule can exclude it
    before = universe.scan(conn, as_of="2024-01-31")  # day 395: still pegged
    assert "coinbase:NEWUSD/USD" not in markets(before) and before["counts"]["low_volatility"] == 1
    assert before["excluded"][0]["vol30"] < universe.MIN_VOL30
    after = universe.scan(conn, as_of="2024-05-14")  # 100 days after the peg broke
    assert "coinbase:NEWUSD/USD" in markets(after)
