"""The exchange collector must build an honest candle history: complete days only (the
in-progress day would leak a not-yet-known close into analysis), the full history back to
listing, no duplicates on re-runs, resumable after failures, impossible candles kept out
but on record, and listing status kept as history for point-in-time universes."""

import ccxt
import pytest

from cryptoagent.collectors.exchange import ExchangeCollector
from tests.conftest import DAY, T0, candle

NOW = T0 + 1000 * DAY + 13 * 3_600_000  # 13:00 UTC on day 1000: day 1000 is still forming


class FakeExchange:
    """Serves candles like the probed exchanges: oldest first, windows by since/limit,
    including the in-progress day, nothing before the first candle."""

    def __init__(self, series, statuses=None, fail_symbol=None, fail_times=0):
        self.series = series  # symbol -> list of candles
        self.statuses = statuses or {}
        self.fail_symbol, self.fail_times = fail_symbol, fail_times
        self.calls = []

    def load_markets(self):
        out = {}
        for symbol in set(self.series) | set(self.statuses):
            base, quote = symbol.split("/")
            status = self.statuses.get(symbol, "online")
            out[symbol] = {"symbol": symbol, "id": symbol.replace("/", "-"), "base": base, "quote": quote,
                           "spot": True, "active": status == "online", "info": {"status": status}}
        out["BTC/EUR"] = {"symbol": "BTC/EUR", "id": "BTC-EUR", "base": "BTC", "quote": "EUR", "spot": True,
                          "active": True, "info": {"status": "online"}}
        return out

    def fetch_ohlcv(self, symbol, timeframe, since, limit):
        self.calls.append((symbol, since))
        if symbol == self.fail_symbol and self.fail_times:
            self.fail_times -= 1
            raise ccxt.NetworkError("connection reset")
        return [list(c) for c in self.series[symbol] if since <= c[0] < since + limit * DAY]


def days(first, last):
    return [candle(T0 + d * DAY, close=100 + d) for d in range(first, last + 1)]


def collector(fake, page_limit=300, now=NOW):
    return ExchangeCollector("coinbase", "coinbaseexchange", "test", page_limit,
                             exchange_factory=lambda: fake, now_ms=lambda: now, sleep=lambda s: None)


def stored(conn, symbol="BTC/USD"):
    return [r[0] for r in conn.execute(
        "SELECT c.ts FROM candles c JOIN markets m ON m.id = c.market_id WHERE m.symbol = ? ORDER BY c.ts", (symbol,))]


def test_backfills_to_listing_and_skips_the_forming_day(conn, settings):
    fake = FakeExchange({"BTC/USD": days(0, 1000)})
    result = collector(fake, page_limit=300).collect(conn, settings)
    ts = stored(conn)
    assert len(ts) == 1000  # days 0..999; day 1000 is still forming at NOW
    assert ts[0] == "2023-11-14T00:00:00Z" and result["candles_added"] == 1000
    assert result["status"] == "succeeded"
    assert conn.execute("SELECT history_start_found_at FROM markets WHERE symbol = 'BTC/USD'").fetchone()[0]


def test_rerun_adds_nothing_and_next_day_adds_one(conn, settings):
    fake = FakeExchange({"BTC/USD": days(0, 1001)})
    collector(fake).collect(conn, settings)
    fake.calls.clear()
    assert collector(fake).collect(conn, settings)["candles_added"] == 0
    assert len(fake.calls) == 0  # history start known and nothing new is complete: no requests
    assert collector(fake, now=NOW + DAY).collect(conn, settings)["candles_added"] == 1
    assert stored(conn)[-1] == "2026-08-10T00:00:00Z"


def test_only_usd_markets_and_inactive_markets_get_status_but_no_candles(conn, settings):
    fake = FakeExchange({"BTC/USD": days(990, 1000), "OLD/USD": days(0, 500)}, statuses={"OLD/USD": "delisted"})
    result = collector(fake).collect(conn, settings)
    assert result["markets_seen"] == 2  # BTC/EUR is not a USD market
    assert stored(conn, "OLD/USD") == []
    rows = conn.execute("SELECT m.symbol, h.status, h.active FROM market_status_history h"
                        " JOIN markets m ON m.id = h.market_id ORDER BY m.symbol").fetchall()
    assert [tuple(r) for r in rows] == [("BTC/USD", "online", 1), ("OLD/USD", "delisted", 0)]


def test_invalid_candles_are_recorded_not_stored(conn, settings):
    series = days(995, 1000)
    series[1] = [series[1][0], 100, 90, 95, 100, 1]  # high below open
    series[2] = [series[2][0], 0, 1, 0, 1, 1]        # zero prices
    result = collector(FakeExchange({"BTC/USD": series})).collect(conn, settings)
    assert result["invalid_candles"] == 2 and len(stored(conn)) == 3
    reasons = [r[0] for r in conn.execute(
        "SELECT json_extract(detail, '$.reason') FROM quality_issues WHERE check_name = 'invalid_ohlc' ORDER BY ts")]
    assert reasons == ["high/low inconsistent with open/close", "non-positive price"]


def test_transient_errors_are_retried(conn, settings):
    fake = FakeExchange({"BTC/USD": days(990, 1000)}, fail_symbol="BTC/USD", fail_times=2)
    assert collector(fake).collect(conn, settings)["status"] == "succeeded"
    assert len(stored(conn)) == 10


def test_a_failing_market_does_not_stop_others_and_resumes_later(conn, settings):
    fake = FakeExchange({"BTC/USD": days(0, 1000), "ETH/USD": days(500, 1000)}, fail_symbol="BTC/USD", fail_times=99)
    result = collector(fake).collect(conn, settings)
    assert result["status"] == "failed" and "BTC/USD" in result["failed"]
    assert len(stored(conn, "ETH/USD")) == 500
    assert conn.execute("SELECT status FROM ingest_runs").fetchone()[0] == "failed"
    fake.fail_times = 0
    assert collector(fake).collect(conn, settings)["status"] == "succeeded"
    assert len(stored(conn)) == 1000


def test_interrupted_backfill_resumes_from_the_earliest_stored_candle(conn, settings):
    fake = FakeExchange({"BTC/USD": days(0, 1000)})

    class StopAfterTwoPages(FakeExchange):
        def fetch_ohlcv(self, symbol, timeframe, since, limit):
            if len(self.calls) == 2:
                raise ccxt.ExchangeError("interrupted")
            return super().fetch_ohlcv(symbol, timeframe, since, limit)

    partial = StopAfterTwoPages(fake.series)
    collector(partial).collect(conn, settings)
    assert len(stored(conn)) == 600  # two pages of 300, newest first
    assert conn.execute("SELECT history_start_found_at FROM markets").fetchone()[0] is None
    collector(fake).collect(conn, settings)
    assert len(stored(conn)) == 1000


def test_markets_option_limits_candles_but_not_status(conn, settings):
    fake = FakeExchange({"BTC/USD": days(990, 1000), "ETH/USD": days(990, 1000)})
    result = collector(fake).collect(conn, settings, markets=["ETH/USD"])
    assert result["markets_collected"] == 1 and stored(conn) == []
    assert conn.execute("SELECT count(*) FROM market_status_history").fetchone()[0] == 2


def test_fixed_symbol_list(conn, settings):
    fake = FakeExchange({"BTC/USD": days(990, 1000), "ETH/USD": days(990, 1000)})
    c = ExchangeCollector("bitstamp", "bitstamp", "test", 1000, symbols=["BTC/USD"],
                          exchange_factory=lambda: fake, now_ms=lambda: NOW)
    assert c.collect(conn, settings)["markets_seen"] == 1
    assert len(stored(conn)) == 10 and stored(conn, "ETH/USD") == []


@pytest.mark.parametrize("now", [NOW, T0 + 1000 * DAY])  # mid-day, and exactly at the UTC day boundary
def test_last_stored_candle_has_fully_closed(conn, settings, now):
    collector(FakeExchange({"BTC/USD": days(990, 1000)}), now=now).collect(conn, settings)
    assert stored(conn)[-1] == "2026-08-09T00:00:00Z"  # day 999 closed at the start of day 1000
