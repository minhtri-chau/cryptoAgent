"""CoinGecko may only be used live: the client must never write to the database, must keep the key
out of URLs and errors, must pick the right coin when several share a symbol, and must carry the
attribution CoinGecko requires."""

import json
from dataclasses import replace

import pytest

from cryptoagent import coingecko
from cryptoagent.config import load_settings

KEY = "CG-secret-123"


class FakeGecko:
    def __init__(self):
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if "/search?" in url:
            return json.dumps({"coins": [{"id": "sol-fake", "symbol": "SOL", "market_cap_rank": 900},
                                         {"id": "solana", "symbol": "SOL", "market_cap_rank": 6},
                                         {"id": "solar", "symbol": "SOLAR", "market_cap_rank": 50}]}).encode()
        return json.dumps([{"id": "solana", "name": "Solana", "current_price": 96.0, "market_cap": 50e9,
                            "market_cap_rank": 6, "fully_diluted_valuation": 60e9, "circulating_supply": 500e6,
                            "total_supply": 600e6, "max_supply": None, "last_updated": "2026-11-01T00:00:00Z"}]).encode()


@pytest.fixture
def settings(tmp_path):
    return replace(load_settings(env_file=tmp_path / "none.env", environ={"COINGECKO_DEMO_API_KEY": KEY}),
                   db_path=tmp_path / "x.db")


def test_live_metrics_pick_the_top_coin_and_keep_the_key_out_of_urls(settings):
    fake = FakeGecko()
    out = coingecko.token_metrics(settings, ["sol"], fetch=fake, delay_s=0)
    sol = out["tokens"]["SOL"]
    assert sol["coingecko_id"] == "solana" and sol["other_coins_with_this_symbol"] == 1
    assert sol["fdv_to_market_cap"] == 1.2 and sol["circulating_share_of_total"] == pytest.approx(0.8333, abs=1e-4)
    assert sol["circulating_share_of_max"] is None  # no max supply: unavailable, not invented
    assert out["attribution"].startswith("Data provided by CoinGecko")
    for url, headers in fake.calls:
        assert KEY not in url and headers == {"x-cg-demo-api-key": KEY}


def test_missing_key_is_a_clear_error_that_never_asks_for_it_in_chat(tmp_path):
    bare = load_settings(env_file=tmp_path / "none.env", environ={})
    with pytest.raises(coingecko.CoinGeckoError, match="add COINGECKO_DEMO_API_KEY.*never paste it in chat"):
        coingecko.token_metrics(bare, ["BTC"], fetch=FakeGecko(), delay_s=0)


def test_nothing_is_written_to_the_database(conn, settings):
    tables_before = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
    coingecko.token_metrics(settings, ["SOL"], fetch=FakeGecko(), delay_s=0)
    assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == tables_before
    assert not hasattr(coingecko, "store")  # by design: no persistence path exists in this module
