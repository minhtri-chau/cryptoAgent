"""Live token metrics from CoinGecko (Demo plan): market cap, FDV and supply. Fetched per question
and never stored.

CoinGecko's API terms (5 Sept 2025) discourage storing data, require any cache to be refreshed
within 24 hours and stored data to be encrypted, and ban storage not expressly permitted (decision
log, 2026-11-01). So nothing here touches the database: callers get the values for the current
conversation only, with the attribution CoinGecko requires.

The key is read from .env (COINGECKO_DEMO_API_KEY), sent only in the x-cg-demo-api-key header, and
never put into a URL, a log line or an error message.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone

from cryptoagent import __version__
from cryptoagent.config import Settings

BASE_URL = "https://api.coingecko.com/api/v3"
KEY_NAME = "COINGECKO_DEMO_API_KEY"
ATTRIBUTION = "Data provided by CoinGecko (https://www.coingecko.com)"
REQUEST_DELAY_S = 1.5  # Demo: 100 calls/min, 10,000/month

Fetch = Callable[[str, dict], bytes]


class CoinGeckoError(ValueError):
    pass


def http_get(url: str, headers: dict) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"cryptoAgent/{__version__}", **headers})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise CoinGeckoError(f"CoinGecko returned HTTP {exc.code} for {url.split('?')[0]}") from None


def token_metrics(settings: Settings, symbols: list[str], fetch: Fetch = http_get,
                  delay_s: float = REQUEST_DELAY_S) -> dict:
    """Market cap, FDV and supply for each symbol, choosing the highest-ranked coin with that symbol."""
    key = settings.secret(KEY_NAME)
    if not key:
        raise CoinGeckoError(f"no CoinGecko key: add {KEY_NAME}=<your demo key> to .env (never paste it in chat)")
    headers = {"x-cg-demo-api-key": key.reveal()}

    def get(path: str, query: dict):
        data = json.loads(fetch(f"{BASE_URL}/{path}?{urllib.parse.urlencode(query)}", headers))
        time.sleep(delay_s)
        return data

    resolved, unresolved = {}, []
    for symbol in dict.fromkeys(s.upper() for s in symbols):
        coins = [c for c in get("search", {"query": symbol}).get("coins", []) if c.get("symbol", "").upper() == symbol]
        ranked = [c for c in coins if c.get("market_cap_rank")]
        if not ranked:
            unresolved.append(symbol)
            continue
        best = min(ranked, key=lambda c: c["market_cap_rank"])
        resolved[best["id"]] = {"symbol": symbol, "other_coins_with_this_symbol": len(coins) - 1}
    rows = get("coins/markets", {"vs_currency": "usd", "ids": ",".join(resolved)}) if resolved else []
    out = {}
    for r in rows:
        meta = resolved.get(r["id"])
        if meta is None:
            continue
        mcap, fdv = r.get("market_cap"), r.get("fully_diluted_valuation")
        circ, total, cap = r.get("circulating_supply"), r.get("total_supply"), r.get("max_supply")
        out[meta["symbol"]] = {
            "coingecko_id": r["id"], "name": r.get("name"), "price": r.get("current_price"),
            "market_cap": mcap, "market_cap_rank": r.get("market_cap_rank"), "fdv": fdv,
            "circulating_supply": circ, "total_supply": total, "max_supply": cap,
            "fdv_to_market_cap": round(fdv / mcap, 4) if fdv and mcap else None,
            "circulating_share_of_max": round(circ / cap, 4) if circ and cap else None,
            "circulating_share_of_total": round(circ / total, 4) if circ and total else None,
            "last_updated": r.get("last_updated"),
            "other_coins_with_this_symbol": meta["other_coins_with_this_symbol"],
        }
    return {"fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "tokens": out,
            "unresolved": unresolved, "attribution": ATTRIBUTION,
            "note": "live values, not stored (CoinGecko terms); FDV / market cap above 1 means future dilution"}
