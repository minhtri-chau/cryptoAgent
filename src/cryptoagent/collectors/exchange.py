"""Daily candles from an exchange's public market-data API, through CCXT. No keys, no trading.

Each run:
- records every selected market's listing status (a row only when it changes)
- for each active market, walks backwards from the earliest stored candle until a whole
  page comes back empty, which marks where the exchange's history starts; this resumes
  after an interruption because progress is committed page by page
- extends forward from the latest stored candle
- stores completed candles only: the current UTC day is still forming, so it is skipped
- rejects candles with impossible values and records them in quality_issues instead

Observed 2026-10-30 (docs/data-sources.md): both exchanges return the in-progress day,
return candles oldest first, and return nothing for windows before a market's first trade.
A gap longer than one page with no trades would end the backfill early; none is known.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable
from typing import Any

from cryptoagent import store
from cryptoagent.config import Settings

TIMEFRAME = "1d"
DAY_MS = store.DAY_MS
RETRIES = 3


def candle_problem(row: list) -> str | None:
    """Why a [ts, open, high, low, close, volume] row can't be a real candle, or None."""
    _, o, h, l, c, v = row
    if any(x is None for x in (o, h, l, c, v)):
        return "missing value"
    if min(o, h, l, c) <= 0:
        return "non-positive price"
    if v < 0:
        return "negative volume"
    if h < max(o, c, l) or l > min(o, c):
        return "high/low inconsistent with open/close"
    return None


class ExchangeCollector:
    source_kind = "exchange"

    def __init__(self, name: str, exchange_id: str, description: str, page_limit: int,
                 symbols: Iterable[str] | None = None, quote: str = "USD",
                 exchange_factory: Callable[[], Any] | None = None,
                 now_ms: Callable[[], int] | None = None, sleep: Callable[[float], None] = time.sleep):
        self.name = name
        self.exchange_id = exchange_id
        self.description = description
        self.page_limit = page_limit
        self.symbols = set(symbols) if symbols else None  # None = every spot market in `quote`
        self.quote = quote
        self._factory = exchange_factory or self._ccxt_exchange
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._sleep = sleep

    def _ccxt_exchange(self):
        import ccxt  # imported lazily so tests and other commands don't pay for it

        return getattr(ccxt, self.exchange_id)({"enableRateLimit": True})

    def configured(self, settings: Settings) -> bool:
        return True  # public market data, no key

    def _selected(self, markets: dict) -> list[dict]:
        if self.symbols is not None:
            return [m for s, m in markets.items() if s in self.symbols]
        return [m for m in markets.values() if m.get("spot") and m.get("quote") == self.quote]

    def _fetch(self, exchange, symbol: str, since: int) -> list[list]:
        import ccxt

        for attempt in range(RETRIES):
            try:
                return exchange.fetch_ohlcv(symbol, TIMEFRAME, since=since, limit=self.page_limit)
            except ccxt.NetworkError:
                if attempt == RETRIES - 1:
                    raise
                self._sleep(5 * (attempt + 1))
        raise AssertionError("unreachable")

    def _store_page(self, conn, market_id: int, rows: list[list], run_id: int) -> tuple[int, int]:
        added = invalid = 0
        with conn:
            for row in rows:
                ts = store.iso_from_ms(row[0])
                problem = candle_problem(row)
                if problem:
                    invalid += store.record_issue(conn, "invalid_ohlc", market_id, ts,
                                                  {"reason": problem, "row": row[1:]}, run_id)
                    continue
                added += conn.execute(
                    "INSERT OR IGNORE INTO candles (market_id, timeframe, ts, open, high, low, close, volume,"
                    " ingest_run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (market_id, TIMEFRAME, ts, *row[1:6], run_id),
                ).rowcount
        return added, invalid

    def _bounds(self, conn, market_id: int) -> tuple[int | None, int | None]:
        lo, hi = conn.execute(
            "SELECT min(ts), max(ts) FROM candles WHERE market_id = ? AND timeframe = ?", (market_id, TIMEFRAME)
        ).fetchone()
        return (store.ms_from_iso(lo) if lo else None, store.ms_from_iso(hi) if hi else None)

    def _collect_market(self, conn, exchange, market_id: int, symbol: str, cutoff: int, run_id: int) -> dict:
        added = invalid = 0
        window = self.page_limit * DAY_MS
        earliest, latest = self._bounds(conn, market_id)
        start_found = conn.execute(
            "SELECT history_start_found_at FROM markets WHERE id = ?", (market_id,)
        ).fetchone()[0]

        if start_found is None or earliest is None:
            end = earliest if earliest is not None else cutoff
            while True:
                since = end - window
                rows = [r for r in self._fetch(exchange, symbol, since) if since <= r[0] < end]
                if not rows:
                    with conn:
                        conn.execute("UPDATE markets SET history_start_found_at = strftime('%Y-%m-%dT%H:%M:%SZ',"
                                     " 'now') WHERE id = ?", (market_id,))
                    break
                a, i = self._store_page(conn, market_id, rows, run_id)
                added, invalid = added + a, invalid + i
                end = min(r[0] for r in rows)
            earliest, latest = self._bounds(conn, market_id)

        since = latest + DAY_MS if latest is not None else cutoff
        while since < cutoff:
            rows = [r for r in self._fetch(exchange, symbol, since) if since <= r[0] < cutoff]
            a, i = self._store_page(conn, market_id, rows, run_id)
            added, invalid = added + a, invalid + i
            since = max(r[0] for r in rows) + DAY_MS if rows else since + window
        return {"added": added, "invalid": invalid}

    def collect(self, conn: sqlite3.Connection, settings: Settings, markets: Iterable[str] | None = None,
                **options) -> dict:
        """Collect daily candles. `markets` limits candle collection to those symbols (status is still recorded)."""
        only = set(markets) if markets else None
        source_id = store.get_or_create_source(conn, self.name, self.source_kind)
        run_id = store.start_run(conn, source_id, {"markets": sorted(only) if only else None})
        cutoff = self._now_ms() // DAY_MS * DAY_MS  # start of the current UTC day: candles before it are complete
        summary = {"run_id": run_id, "source": self.name, "markets_seen": 0, "status_changes": 0,
                   "markets_collected": 0, "candles_added": 0, "invalid_candles": 0, "failed": {}}
        try:
            exchange = self._factory()
            selected = self._selected(exchange.load_markets())
            ids = {}
            with conn:
                for m in selected:
                    market_id = store.get_or_create_market(conn, source_id, m["base"], m["quote"], m["symbol"], m["id"])
                    info = m.get("info") or {}
                    status = str(info.get("status") or ("online" if m.get("active") else "offline"))
                    summary["status_changes"] += store.record_status(conn, market_id, status, bool(m.get("active")),
                                                                     run_id)
                    if m.get("active") and (only is None or m["symbol"] in only):
                        ids[m["symbol"]] = market_id
            summary["markets_seen"] = len(selected)
            for symbol in sorted(ids):
                try:
                    result = self._collect_market(conn, exchange, ids[symbol], symbol, cutoff, run_id)
                except Exception as exc:  # one failing market must not stop the others
                    summary["failed"][symbol] = f"{type(exc).__name__}: {exc}"[:300]
                    continue
                summary["markets_collected"] += 1
                summary["candles_added"] += result["added"]
                summary["invalid_candles"] += result["invalid"]
        except Exception as exc:
            store.finish_run(conn, run_id, summary["candles_added"], f"{type(exc).__name__}: {exc}")
            raise
        error = f"{len(summary['failed'])} market(s) failed" if summary["failed"] else None
        store.finish_run(conn, run_id, summary["candles_added"], error)
        summary["status"] = "failed" if error else "succeeded"
        return summary
