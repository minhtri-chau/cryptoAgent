"""Candles from an exchange's public market-data API, through CCXT. No keys, no trading.

Each run:
- records every selected market's listing status (a row only when it changes)
- for each active market, walks backwards from the earliest stored candle until a whole
  page comes back empty, which marks where the exchange's history starts; this resumes
  after an interruption because progress is committed page by page
- extends forward from the latest stored candle
- with include_inactive, also stores the full history of delisted markets once: their
  last page is fetched without a start time, then the same backward walk runs
- stores completed candles only: the current bar (UTC day or hour) is still forming
- rejects candles with impossible values and records them in quality_issues instead

Backfill progress is kept per market and timeframe in market_backfill.

Observed 2026-10-30 (docs/data-sources.md): both exchanges return the in-progress bar,
return candles oldest first, return nothing for windows before a market's first trade,
and Coinbase still serves candles for delisted products. A gap longer than one page with
no trades would end the backfill early; none is known.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable
from typing import Any

from cryptoagent import store
from cryptoagent.config import Settings

BAR_MS = {"1d": store.DAY_MS, "1h": 3_600_000}
RETRIES = 3
MARKETS_BACKOFF_S = (10, 30, 60)  # a scheduled run right after the PC wakes can start before the network is up


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
                 symbols: Iterable[str] | None = None, quote: str = "USD", timeframe: str = "1d",
                 source: str | None = None, include_inactive: bool = False,
                 exchange_factory: Callable[[], Any] | None = None,
                 now_ms: Callable[[], int] | None = None, sleep: Callable[[float], None] = time.sleep):
        if timeframe not in BAR_MS:
            raise ValueError(f"unsupported timeframe {timeframe!r}")
        self.name = name
        self.source = source or name  # e.g. collector 'coinbase-1h' writes markets of source 'coinbase'
        self.exchange_id = exchange_id
        self.description = description
        self.page_limit = page_limit
        self.symbols = set(symbols) if symbols else None  # None = every spot market in `quote`
        self.quote = quote
        self.timeframe = timeframe
        self.bar_ms = BAR_MS[timeframe]
        self.include_inactive = include_inactive
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

    def _load_markets(self, exchange) -> dict:
        import ccxt

        for wait in (*MARKETS_BACKOFF_S, None):
            try:
                return exchange.load_markets()
            except ccxt.NetworkError:
                if wait is None:
                    raise
                self._sleep(wait)
        raise AssertionError("unreachable")

    def _fetch(self, exchange, symbol: str, since: int | None) -> list[list]:
        import ccxt

        for attempt in range(RETRIES):
            try:
                return exchange.fetch_ohlcv(symbol, self.timeframe, since=since, limit=self.page_limit)
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
                                                  {"reason": problem, "timeframe": self.timeframe,
                                                   "row": row[1:]}, run_id)
                    continue
                added += conn.execute(
                    "INSERT OR IGNORE INTO candles (market_id, timeframe, ts, open, high, low, close, volume,"
                    " ingest_run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (market_id, self.timeframe, ts, *row[1:6], run_id),
                ).rowcount
        return added, invalid

    def _bounds(self, conn, market_id: int) -> tuple[int | None, int | None]:
        lo, hi = conn.execute(
            "SELECT min(ts), max(ts) FROM candles WHERE market_id = ? AND timeframe = ?", (market_id, self.timeframe)
        ).fetchone()
        return (store.ms_from_iso(lo) if lo else None, store.ms_from_iso(hi) if hi else None)

    def _start_found(self, conn, market_id: int) -> bool:
        return conn.execute("SELECT 1 FROM market_backfill WHERE market_id = ? AND timeframe = ?",
                            (market_id, self.timeframe)).fetchone() is not None

    def _collect_market(self, conn, exchange, market_id: int, symbol: str, cutoff: int, run_id: int,
                        active: bool) -> dict:
        added = invalid = 0
        window = self.page_limit * self.bar_ms
        earliest, latest = self._bounds(conn, market_id)

        if not active and earliest is None:
            # A delisted market's history ends before today: start from its last page.
            rows = [r for r in self._fetch(exchange, symbol, None) if r[0] < cutoff]
            added, invalid = self._store_page(conn, market_id, rows, run_id)
            earliest, latest = self._bounds(conn, market_id)

        if not self._start_found(conn, market_id) or (earliest is None and active):
            end = earliest if earliest is not None else cutoff
            while True:
                since = end - window
                rows = [r for r in self._fetch(exchange, symbol, since) if since <= r[0] < end]
                if not rows:
                    with conn:
                        conn.execute("INSERT OR REPLACE INTO market_backfill (market_id, timeframe,"
                                     " history_start_found_at) VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
                                     (market_id, self.timeframe))
                    break
                a, i = self._store_page(conn, market_id, rows, run_id)
                added, invalid = added + a, invalid + i
                end = min(r[0] for r in rows)
            earliest, latest = self._bounds(conn, market_id)

        if active:
            since = latest + self.bar_ms if latest is not None else cutoff
            while since < cutoff:
                rows = [r for r in self._fetch(exchange, symbol, since) if since <= r[0] < cutoff]
                a, i = self._store_page(conn, market_id, rows, run_id)
                added, invalid = added + a, invalid + i
                since = max(r[0] for r in rows) + self.bar_ms if rows else since + window
        return {"added": added, "invalid": invalid}

    def collect(self, conn: sqlite3.Connection, settings: Settings, markets: Iterable[str] | None = None,
                **options) -> dict:
        """Collect candles. `markets` limits candle collection to those symbols (status is still recorded)."""
        only = set(markets) if markets else None
        source_id = store.get_or_create_source(conn, self.source, self.source_kind)
        run_id = store.start_run(conn, source_id, {"collector": self.name, "timeframe": self.timeframe,
                                                   "markets": sorted(only) if only else None})
        cutoff = self._now_ms() // self.bar_ms * self.bar_ms  # start of the forming bar: earlier bars are complete
        summary = {"run_id": run_id, "source": self.source, "timeframe": self.timeframe, "markets_seen": 0,
                   "status_changes": 0, "markets_collected": 0, "inactive_backfilled": 0, "candles_added": 0,
                   "invalid_candles": 0, "failed": {}}
        try:
            exchange = self._factory()
            selected = self._selected(self._load_markets(exchange))
            todo = {}
            with conn:
                for m in selected:
                    market_id = store.get_or_create_market(conn, source_id, m["base"], m["quote"], m["symbol"], m["id"])
                    info = m.get("info") or {}
                    active = bool(m.get("active"))
                    status = str(info.get("status") or ("online" if active else "offline"))
                    summary["status_changes"] += store.record_status(conn, market_id, status, active, run_id)
                    if only is not None and m["symbol"] not in only:
                        continue
                    if active or (self.include_inactive and not self._start_found(conn, market_id)):
                        todo[m["symbol"]] = (market_id, active)
            summary["markets_seen"] = len(selected)
            for symbol in sorted(todo):
                market_id, active = todo[symbol]
                try:
                    result = self._collect_market(conn, exchange, market_id, symbol, cutoff, run_id, active)
                except Exception as exc:  # one failing market must not stop the others
                    summary["failed"][symbol] = f"{type(exc).__name__}: {exc}"[:300]
                    continue
                summary["markets_collected" if active else "inactive_backfilled"] += 1
                summary["candles_added"] += result["added"]
                summary["invalid_candles"] += result["invalid"]
        except Exception as exc:
            store.finish_run(conn, run_id, summary["candles_added"], f"{type(exc).__name__}: {exc}")
            raise
        error = f"{len(summary['failed'])} market(s) failed" if summary["failed"] else None
        store.finish_run(conn, run_id, summary["candles_added"], error)
        summary["status"] = "failed" if error else "succeeded"
        return summary
