"""Shared writes for collectors: sources, assets, markets, ingest runs and quality issues."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

DAY_MS = 86_400_000


def iso_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms_from_iso(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def get_or_create_source(conn: sqlite3.Connection, name: str, kind: str) -> int:
    conn.execute("INSERT OR IGNORE INTO sources (name, kind) VALUES (?, ?)", (name, kind))
    return conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()[0]


def get_or_create_asset(conn: sqlite3.Connection, symbol: str) -> int:
    conn.execute("INSERT OR IGNORE INTO assets (symbol) VALUES (?)", (symbol.upper(),))
    return conn.execute("SELECT id FROM assets WHERE symbol = ?", (symbol.upper(),)).fetchone()[0]


def get_or_create_market(conn: sqlite3.Connection, source_id: int, base: str, quote: str, symbol: str,
                         venue_id: str) -> int:
    asset_id = get_or_create_asset(conn, base)
    conn.execute(
        "INSERT OR IGNORE INTO markets (source_id, base_asset_id, quote, symbol, venue_id) VALUES (?, ?, ?, ?, ?)",
        (source_id, asset_id, quote.upper(), symbol, venue_id),
    )
    return conn.execute("SELECT id FROM markets WHERE source_id = ? AND symbol = ?", (source_id, symbol)).fetchone()[0]


def record_status(conn: sqlite3.Connection, market_id: int, status: str, active: bool, run_id: int) -> bool:
    """Append a status row when it differs from the latest one. Returns True if it changed."""
    last = conn.execute(
        "SELECT status, active FROM market_status_history WHERE market_id = ? ORDER BY id DESC LIMIT 1", (market_id,)
    ).fetchone()
    if last is not None and (last["status"], bool(last["active"])) == (status, active):
        return False
    conn.execute(
        "INSERT INTO market_status_history (market_id, status, active, ingest_run_id) VALUES (?, ?, ?, ?)",
        (market_id, status, int(active), run_id),
    )
    return True


def start_run(conn: sqlite3.Connection, source_id: int, options: dict) -> int:
    with conn:
        return conn.execute(
            "INSERT INTO ingest_runs (source_id, options) VALUES (?, ?)", (source_id, json.dumps(options, sort_keys=True))
        ).lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, row_count: int, error: str | None = None) -> None:
    with conn:
        conn.execute(
            "UPDATE ingest_runs SET finished_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), status = ?, row_count = ?,"
            " error = ? WHERE id = ?",
            ("failed" if error else "succeeded", row_count, error, run_id),
        )


def record_issue(conn: sqlite3.Connection, check_name: str, market_id: int, ts: str, detail: dict,
                 run_id: int | None = None) -> bool:
    """Record a data problem once. Returns True if it is new."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO quality_issues (check_name, market_id, ts, detail, ingest_run_id) VALUES (?, ?, ?, ?, ?)",
        (check_name, market_id, ts, json.dumps(detail, sort_keys=True), run_id),
    )
    return cur.rowcount == 1
