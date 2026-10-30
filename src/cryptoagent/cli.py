"""Command-line entry point: `python -m cryptoagent <command>`.

Commands that read data print JSON so skills and future AI runners can consume them
directly. Validation problems exit with status 2.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

from cryptoagent import db, quality
from cryptoagent.collectors import COLLECTORS
from cryptoagent.config import load_settings


class UsageError(Exception):
    pass


def _out(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cryptoagent")
    parser.add_argument("--db", help="database path (default: CRYPTOAGENT_DB_PATH or data/cryptoagent.db)")
    cmd = parser.add_subparsers(dest="command", required=True)

    cmd.add_parser("init-db", help="create the database and apply pending migrations")
    cmd.add_parser("db-status", help="applied/pending migrations and tables")
    cmd.add_parser("sources", help="list data collectors and whether each is configured")

    p = cmd.add_parser("collect", help="run a collector: backfills history, then extends to the last complete day")
    p.add_argument("name", choices=list(COLLECTORS))
    p.add_argument("--markets", help="comma-separated symbols to collect candles for, e.g. BTC/USD,ETH/USD")

    p = cmd.add_parser("quality", help="run data-quality checks and summarise recorded issues")
    p.add_argument("--no-run", action="store_true", help="only summarise issues already recorded")
    p.add_argument("--market", help="limit the summary to one market, e.g. coinbase:BTC/USD")
    p.add_argument("--limit", type=int, default=20, help="number of recent issues to list")
    p.add_argument("--outlier-return", type=float, default=quality.OUTLIER_RETURN)
    p.add_argument("--reference-tolerance", type=float, default=quality.REFERENCE_TOLERANCE)

    p = cmd.add_parser("markets", help="markets with latest listing status and stored candle range")
    p.add_argument("--source")
    p.add_argument("--status", help="latest exchange status, e.g. online, delisted")

    p = cmd.add_parser("candles", help="stored daily candles for one market")
    p.add_argument("market", help="SOURCE:SYMBOL, e.g. coinbase:BTC/USD")
    p.add_argument("--last", type=int, default=30, help="number of most recent candles")
    return parser


def _market_id(conn, spec: str) -> int:
    source, _, symbol = spec.partition(":")
    row = conn.execute(
        "SELECT m.id FROM markets m JOIN sources s ON s.id = m.source_id WHERE s.name = ? AND m.symbol = ?",
        (source, symbol),
    ).fetchone()
    if row is None:
        raise UsageError(f"unknown market {spec!r}; expected SOURCE:SYMBOL, see `markets`")
    return row[0]


def list_markets(conn, source: str | None, status: str | None) -> list[dict]:
    rows = conn.execute(
        """
        SELECT s.name AS source, m.symbol, m.venue_id, h.status, h.active, h.observed_at AS status_since,
               m.history_start_found_at, c.first_ts, c.last_ts, coalesce(c.n, 0) AS candles
        FROM markets m
        JOIN sources s ON s.id = m.source_id
        LEFT JOIN market_status_history h
               ON h.id = (SELECT max(id) FROM market_status_history WHERE market_id = m.id)
        LEFT JOIN (SELECT market_id, min(ts) AS first_ts, max(ts) AS last_ts, count(*) AS n
                   FROM candles WHERE timeframe = '1d' GROUP BY market_id) c ON c.market_id = m.id
        WHERE (? IS NULL OR s.name = ?) AND (? IS NULL OR h.status = ?)
        ORDER BY s.name, m.symbol
        """,
        (source, source, status, status),
    ).fetchall()
    return [dict(r) for r in rows]


def run(args, conn: sqlite3.Connection, settings) -> None:
    c = args.command
    if c == "init-db":
        _out({"applied": db.migrate(conn), "db": str(settings.db_path)})
    elif c == "db-status":
        _out(db.status(conn))
    elif c == "sources":
        _out([{"name": k, "kind": v.source_kind, "description": v.description, "configured": v.configured(settings)}
              for k, v in COLLECTORS.items()])
    elif c == "collect":
        markets = [s.strip() for s in args.markets.split(",") if s.strip()] if args.markets else None
        _out(COLLECTORS[args.name].collect(conn, settings, markets=markets))
    elif c == "quality":
        new = None if args.no_run else quality.run_checks(conn, args.outlier_return, args.reference_tolerance)
        if args.market:
            _market_id(conn, args.market)
        _out({"new_issues": new, **quality.summary(conn, args.market, args.limit)})
    elif c == "markets":
        _out(list_markets(conn, args.source, args.status))
    elif c == "candles":
        rows = conn.execute(
            "SELECT ts, open, high, low, close, volume FROM candles WHERE market_id = ? AND timeframe = '1d'"
            " ORDER BY ts DESC LIMIT ?",
            (_market_id(conn, args.market), args.last),
        ).fetchall()
        _out([dict(r) for r in reversed(rows)])


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    if args.db:
        settings = replace(settings, db_path=Path(args.db))
    conn = db.connect(settings.db_path)
    try:
        if args.command not in ("init-db", "db-status") and db.status(conn)["pending"]:
            print("database has pending migrations; run `python -m cryptoagent init-db` first", file=sys.stderr)
            return 2
        run(args, conn, settings)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except sqlite3.IntegrityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
