"""Fundamentals add context to altcoin research, so they must be attributed to the right token
(a protocol family like Aave V2 + V3 is one token), known only from the day we saw them (no
back-filled history pretending to be point-in-time), collected once a day, and never edited."""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from cryptoagent import fundamentals, store
from cryptoagent.collectors.defillama import DefiLlamaCollector, aggregate

PROTOCOLS = [
    {"name": "Aave V2", "slug": "aave-v2", "parentProtocol": "parent#aave", "symbol": "AAVE", "tvl": 100.0,
     "mcap": 3000.0, "category": "Lending"},
    {"name": "Aave V3", "slug": "aave-v3", "parentProtocol": "parent#aave", "symbol": "AAVE", "tvl": 900.0,
     "mcap": 3000.0, "category": "Lending"},
    {"name": "Old Thing", "slug": "old", "symbol": "AAVE", "tvl": 50.0, "deprecated": True},
    {"name": "Curve DEX", "slug": "curve-dex", "symbol": "CRV", "tvl": 400.0, "mcap": None, "category": "Dexs"},
    {"name": "No Token", "slug": "nt", "symbol": "-", "tvl": 10.0},
    {"name": "Unlisted", "slug": "unl", "symbol": "ZZZ", "tvl": 10.0},
]
FEES = [{"slug": "aave-v2", "parentProtocol": "parent#aave", "total24h": 1.0, "total30d": 30.0},
        {"slug": "aave-v3", "parentProtocol": "parent#aave", "total24h": 9.0, "total30d": 270.0},
        {"slug": "curve-dex", "total24h": 2.0, "total30d": 60.0}]
REVENUE = [{"slug": "aave-v3", "parentProtocol": "parent#aave", "total24h": 1.0, "total30d": 25.0}]


def test_families_are_summed_and_only_listed_tokens_kept():
    out = aggregate(PROTOCOLS, FEES, REVENUE, {"AAVE", "CRV"})
    assert set(out) == {"parent#aave", "curve-dex"}  # no-token and unlisted dropped
    aave = out["parent#aave"]
    assert (aave["name"], aave["tvl"], aave["mcap"], aave["fees_30d"], aave["revenue_30d"]) == \
        ("aave", 1000.0, 3000.0, 300.0, 25.0)  # deprecated protocol excluded, V2 + V3 summed
    assert "mcap" not in out["curve-dex"]


class FakeLlama:
    def __init__(self):
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url.endswith("/protocols"):
            return json.dumps(PROTOCOLS).encode()
        return json.dumps({"protocols": REVENUE if "dailyRevenue" in url else FEES}).encode()


@pytest.fixture
def listed(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    for base in ("AAVE", "CRV"):
        store.get_or_create_market(conn, source_id, base, "USD", f"{base}/USD", f"{base}-USD")
    return conn


def collect(conn, settings, day):
    when = datetime.fromisoformat(f"{day}T01:00:00+00:00")
    return DefiLlamaCollector(fetch=FakeLlama(), delay_s=0, now=lambda: when).collect(conn, settings)


def test_one_snapshot_per_day_append_only(listed, settings):
    first = collect(listed, settings, "2026-10-01")
    assert first["families"] == 2 and first["values_added"] == 9  # aave: 6 metrics, curve: tvl + 2 fees
    assert collect(listed, settings, "2026-10-01")["values_added"] == 0  # rerun the same day
    with pytest.raises(sqlite3.IntegrityError):
        listed.execute("UPDATE fundamental_obs SET value = 0")


def test_values_are_known_only_from_their_snapshot_day_and_ratios_computed(listed, settings):
    collect(listed, settings, "2026-09-01")
    collect(listed, settings, "2026-10-01")
    assert fundamentals.for_symbol(listed, "AAVE", as_of="2026-08-31") == []  # nothing back-filled
    row = fundamentals.for_symbol(listed, "aave")[0]
    assert (row["snapshot_day"], row["tracked_since"], row["snapshots"]) == ("2026-10-01", "2026-09-01", 2)
    assert row["price_to_fees"] == pytest.approx(round(3000 / (300 * 365 / 30), 4))
    assert row["tvl_change_30d"] == 0.0
    assert fundamentals.for_symbol(listed, "CRV")[0]["price_to_fees"] is None  # no market cap: unavailable
    assert fundamentals.summary_by_symbol(listed)["AAVE"]["family"] == "parent#aave"


def test_treasury_holding_the_token_is_not_its_fundamentals():
    treasury = [{"name": "Ethereum Foundation", "slug": "ethereum-foundation", "symbol": "ETH", "tvl": 0.0,
                 "mcap": 274e9, "category": "Treasury"}]
    assert aggregate(treasury, [], [], {"ETH"}) == {}


def test_scan_may_use_the_snapshot_taken_the_morning_after(listed, settings):
    collect(listed, settings, "2026-10-31")
    assert "AAVE" in fundamentals.summary_by_symbol(listed, as_of="2026-10-30")
    assert fundamentals.summary_by_symbol(listed, as_of="2026-10-29") == {}  # two days later: not yet known
