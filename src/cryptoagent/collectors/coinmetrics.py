"""Coin Metrics Community API: daily reference prices used to cross-check exchange closes.

Free, no key, CC BY-NC 4.0 (non-commercial use, attribution "Coin Metrics" required).
Limit 10 requests per rolling 6 s per IP. PriceUSD for BTC starts 2010-07-18; the value
for day D lines up with the close of the exchange candle that starts on D (checked
2026-10-30 against Coinbase). Only PriceUSD has long history on the community tier.
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from cryptoagent import __version__, store
from cryptoagent.config import Settings

BASE_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
USER_AGENT = f"cryptoAgent/{__version__}"
METRIC = "PriceUSD"
ASSETS = ("btc",)
PAGE_SIZE = 10_000
REQUEST_DELAY_S = 0.7  # stays under 10 requests per 6 s

Fetch = Callable[[str], bytes]


def http_get(url: str, retries: int = 3) -> bytes:
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if (exc.code < 500 and exc.code != 429) or attempt == retries - 1:
                raise
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
        time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


class CoinMetricsCollector:
    name = "coinmetrics"
    source_kind = "reference"
    description = "Coin Metrics Community PriceUSD (daily reference price, CC BY-NC 4.0)"

    def __init__(self, fetch: Fetch = http_get, delay_s: float = REQUEST_DELAY_S):
        self._fetch = fetch
        self._delay_s = delay_s

    def configured(self, settings: Settings) -> bool:
        return True  # public data, no key

    def collect(self, conn: sqlite3.Connection, settings: Settings, **options) -> dict:
        source_id = store.get_or_create_source(conn, self.name, self.source_kind)
        run_id = store.start_run(conn, source_id, {"assets": list(ASSETS), "metric": METRIC})
        added = 0
        try:
            for asset in ASSETS:
                asset_id = store.get_or_create_asset(conn, asset)
                last = conn.execute(
                    "SELECT max(day) FROM reference_prices WHERE asset_id = ? AND source_id = ? AND metric = ?",
                    (asset_id, source_id, METRIC),
                ).fetchone()[0]
                query = {"assets": asset, "metrics": METRIC, "frequency": "1d", "page_size": PAGE_SIZE,
                         "paging_from": "start"}
                if last:
                    query["start_time"] = last
                url = f"{BASE_URL}?{urllib.parse.urlencode(query)}"
                while url:
                    page = json.loads(self._fetch(url))
                    with conn:
                        for point in page.get("data", []):
                            if point.get(METRIC) is None:
                                continue
                            added += conn.execute(
                                "INSERT OR IGNORE INTO reference_prices (asset_id, source_id, metric, day, value,"
                                " ingest_run_id) VALUES (?, ?, ?, ?, ?, ?)",
                                (asset_id, source_id, METRIC, point["time"][:10], float(point[METRIC]), run_id),
                            ).rowcount
                    url = page.get("next_page_url")
                    if url:
                        time.sleep(self._delay_s)
        except Exception as exc:
            store.finish_run(conn, run_id, added, f"{type(exc).__name__}: {exc}")
            raise
        store.finish_run(conn, run_id, added)
        return {"run_id": run_id, "source": self.name, "values_added": added, "status": "succeeded"}
