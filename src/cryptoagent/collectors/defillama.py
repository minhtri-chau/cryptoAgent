"""DefiLlama: daily point-in-time snapshots of protocol fundamentals (TVL, market cap, fees, revenue).

Terms (defillama.com/terms, 24 June 2025): a "personal, non-commercial" licence to the official
public API; storing is not addressed. The user accepted the risk of storing privately on
2026-10-31 (decision log): the database stays private, is never republished, and collection
is at most once a day.

Each run makes three requests (about 20 MB in total):
- /protocols: TVL, market cap, token symbol and family (parentProtocol) of every protocol
- /overview/fees and /overview/fees?dataType=dailyRevenue: trailing 24h and 30d totals
Protocols are summed per family (Aave V2 + V3 = 'parent#aave'). Only families whose token
symbol is a Coinbase USD market, and that show activity (TVL or fees above zero), are stored: an
entity that merely holds a token (the Ethereum Foundation treasury reports symbol ETH) is not that
token's fundamentals. Chains are not protocols here, so L1 fees (ETH, SOL) are absent. No history
is backfilled.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone

from cryptoagent import store
from cryptoagent.collectors.coinmetrics import http_get
from cryptoagent.config import Settings

BASE_URL = "https://api.llama.fi"
FEES_QUERY = "excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
REQUEST_DELAY_S = 2.0
NO_TOKEN = {"", "-"}

Fetch = Callable[[str], bytes]


def family_key(protocol: dict) -> str:
    return protocol.get("parentProtocol") or protocol.get("slug") or protocol.get("name", "")


def aggregate(protocols: list[dict], fees: list[dict], revenue: list[dict], symbols: set[str]) -> dict[str, dict]:
    """Sum protocols into families and keep families whose token symbol is in `symbols`."""
    families: dict[str, dict] = {}
    for p in protocols:
        if p.get("deprecated"):
            continue
        fam = families.setdefault(family_key(p), {"names": [], "symbols": {}, "category": p.get("category"),
                                                   "tvl": 0.0, "mcap": None})
        fam["names"].append(p.get("name", ""))
        symbol = (p.get("symbol") or "").strip().upper()
        if symbol not in NO_TOKEN:
            fam["symbols"][symbol] = fam["symbols"].get(symbol, 0) + 1
        fam["tvl"] += float(p.get("tvl") or 0)
        if p.get("mcap"):
            fam["mcap"] = max(fam["mcap"] or 0.0, float(p["mcap"]))  # every member reports the same token
    for rows, prefix in ((fees, "fees"), (revenue, "revenue")):
        for r in rows:
            fam = families.get(r.get("parentProtocol") or r.get("slug") or "")
            if fam is None:
                continue
            for window in ("24h", "30d"):
                value = r.get(f"total{window}")
                if value is not None:
                    fam[f"{prefix}_{window}"] = fam.get(f"{prefix}_{window}", 0.0) + float(value)
    out = {}
    for key, fam in families.items():
        if not fam["symbols"]:
            continue
        symbol = max(fam["symbols"], key=lambda s: fam["symbols"][s])  # the family's most-reported token
        active = fam["tvl"] > 0 or any(fam.get(m) for m in ("fees_30d", "revenue_30d"))
        if symbol in symbols and active:  # a treasury holding the token (zero TVL, no fees) is not its fundamentals
            name = key.removeprefix("parent#") if key.startswith("parent#") else fam["names"][0]
            out[key] = {"name": name, "symbol": symbol, "category": fam["category"], "tvl": fam["tvl"],
                        **{m: fam[m] for m in ("mcap", "fees_24h", "fees_30d", "revenue_24h", "revenue_30d")
                           if fam.get(m) is not None}}
    return out


class DefiLlamaCollector:
    name = "defillama"
    source_kind = "reference"
    description = "DefiLlama daily fundamentals snapshot (TVL, mcap, fees, revenue) for Coinbase-listed tokens"

    def __init__(self, fetch: Fetch = http_get, delay_s: float = REQUEST_DELAY_S,
                 now: Callable[[], datetime] | None = None):
        self._fetch = fetch
        self._delay_s = delay_s
        self._now = now or (lambda: datetime.now(timezone.utc))

    def configured(self, settings: Settings) -> bool:
        return True  # public API, no key

    def _json(self, path: str):
        data = json.loads(self._fetch(f"{BASE_URL}/{path}"))
        time.sleep(self._delay_s)
        return data

    def collect(self, conn: sqlite3.Connection, settings: Settings, **options) -> dict:
        source_id = store.get_or_create_source(conn, self.name, self.source_kind)
        run_id = store.start_run(conn, source_id, {"endpoints": ["protocols", "overview/fees", "overview/revenue"]})
        day = self._now().date().isoformat()
        added = 0
        try:
            symbols = {r[0] for r in conn.execute(
                "SELECT DISTINCT a.symbol FROM markets m JOIN assets a ON a.id = m.base_asset_id"
                " JOIN sources s ON s.id = m.source_id WHERE s.name = 'coinbase' AND m.quote = 'USD'")}
            protocols = self._json("protocols")
            fees = self._json(f"overview/fees?{FEES_QUERY}").get("protocols", [])
            revenue = self._json(f"overview/fees?{FEES_QUERY}&dataType=dailyRevenue").get("protocols", [])
            families = aggregate(protocols, fees, revenue, symbols)
            with conn:
                for key, fam in families.items():
                    conn.execute("INSERT OR IGNORE INTO fundamental_entities (source_id, key, name, symbol, category)"
                                 " VALUES (?, ?, ?, ?, ?)", (source_id, key, fam["name"], fam["symbol"], fam["category"]))
                    entity_id = conn.execute("SELECT id FROM fundamental_entities WHERE source_id = ? AND key = ?",
                                             (source_id, key)).fetchone()[0]
                    for metric in ("tvl", "mcap", "fees_24h", "fees_30d", "revenue_24h", "revenue_30d"):
                        if fam.get(metric) is not None and fam[metric] >= 0:
                            added += conn.execute(
                                "INSERT OR IGNORE INTO fundamental_obs (entity_id, metric, day, value, ingest_run_id)"
                                " VALUES (?, ?, ?, ?, ?)", (entity_id, metric, day, fam[metric], run_id)).rowcount
        except Exception as exc:
            store.finish_run(conn, run_id, added, f"{type(exc).__name__}: {exc}")
            raise
        store.finish_run(conn, run_id, added)
        return {"run_id": run_id, "source": self.name, "day": day, "families": len(families), "values_added": added,
                "status": "succeeded"}
