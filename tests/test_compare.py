"""A comparison is only fair if every coin gets exactly the same measurements, computed the same
way, with missing data shown as missing rather than guessed, no score or ranking slipped in, and
incomparable fundamentals (zero-TVL families, L1s) flagged rather than set side by side."""

import numpy as np
import pandas as pd
import pytest

from cryptoagent import compare, store


@pytest.fixture
def coins(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    run_id = store.start_run(conn, source_id, {})
    rng = np.random.default_rng(0)
    common = rng.normal(0, 0.03, 700)
    for symbol, noise in (("BTC", 0.0), ("AAA", 0.01), ("BBB", 0.03)):
        market_id = store.get_or_create_market(conn, source_id, symbol, "USD", f"{symbol}/USD", f"{symbol}-USD")
        close = 100 * np.exp(np.cumsum(common + rng.normal(0, noise, 700)))
        days = pd.date_range("2024-01-01", periods=700, freq="D")
        with conn:
            conn.executemany(
                "INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                " VALUES (?, '1d', ?, ?, ?, ?, ?, 100000, ?)",
                [(market_id, d.strftime("%Y-%m-%dT00:00:00Z"), c, c * 1.01, c * 0.99, c, run_id)
                 for d, c in zip(days, close)])
    return conn


def test_same_fields_for_every_coin_and_no_scores(coins):
    out = compare.compare(coins, ["aaa", "bbb", "ccc"], horizon=90)
    available = [c for c in out["coins"] if c["available"]]
    assert [c["symbol"] for c in available] == ["AAA", "BBB"]
    assert set(available[0]) == set(available[1])  # identical criteria
    assert out["coins"][2] == {"symbol": "CCC", "available": False, "note": "no coinbase:CCC/USD candles stored"}
    text = str(out).lower()
    assert "score" not in text.replace("no scores", "") and "rank" not in text.replace("no ranking", "")


def test_correlation_reflects_shared_moves(coins):
    corr = compare.compare(coins, ["AAA", "BBB"], horizon=90)["correlation_daily_returns"]
    assert corr["days"] == 365 and 0.5 < corr["matrix"]["AAA"]["BBB"] < 1.0  # driven by the common factor
    assert corr["matrix"]["AAA"]["AAA"] == 1.0


def test_missing_live_data_is_null_not_guessed(coins):
    out = compare.compare(coins, ["AAA", "BBB"], horizon=90)
    assert out["coins"][0]["fundamentals"]["market_cap"] is None  # no live figures given: unavailable, not guessed
    with_live = compare.compare(coins, ["AAA", "BBB"], horizon=90, tokens={"AAA": {"market_cap": 1e9,
                                                                                  "fdv_to_market_cap": 1.5}})
    assert with_live["coins"][0]["fundamentals"]["fdv_to_market_cap"] == 1.5


def test_needs_two_to_six_coins(coins):
    with pytest.raises(ValueError):
        compare.compare(coins, ["AAA"])


def test_zero_tvl_family_is_flagged_as_not_comparable(coins):
    """e.g. DefiLlama's "Solana" entry: chain-level fees with zero TVL must not sit beside a DeFi protocol's multiple."""
    source_id = store.get_or_create_source(coins, "defillama", "reference")
    run_id = store.start_run(coins, source_id, {})
    with coins:
        entity = coins.execute("INSERT INTO fundamental_entities (source_id, key, name, symbol) VALUES (?, 'aaa',"
                               " 'Aaa Chain', 'AAA')", (source_id,)).lastrowid
        for metric, value in (("tvl", 0.0), ("fees_30d", 3e6), ("revenue_30d", 1e6)):
            coins.execute("INSERT INTO fundamental_obs (entity_id, metric, day, value, ingest_run_id)"
                          " VALUES (?, ?, '2025-11-30', ?, ?)", (entity, metric, value, run_id))
    out = compare.compare(coins, ["AAA", "BBB"], horizon=90, tokens={"AAA": {"market_cap": 1e9}})
    f = out["coins"][0]["fundamentals"]
    assert f["market_cap_to_revenue_comparable"] is False and "zero TVL" in f["note"]
    assert f["market_cap_to_revenue"] == pytest.approx(1e9 / (1e6 * 365 / 30), abs=0.01)  # shown, but flagged
