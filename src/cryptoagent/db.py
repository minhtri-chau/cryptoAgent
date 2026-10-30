"""SQLite connection and numbered-migration runner."""

from __future__ import annotations

import re
import sqlite3
from importlib import resources
from pathlib import Path

_MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def available_migrations() -> list[tuple[int, str, str]]:
    """Return (version, filename, sql) for every bundled migration, in order."""
    found = []
    for entry in resources.files("cryptoagent.migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    return sorted(found)


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))"
    )
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations, each in its own transaction. Returns names applied."""
    done = applied_versions(conn)
    applied = []
    for version, name, sql in available_migrations():
        if version in done:
            continue
        # executescript commits first, so wrap the migration and its record explicitly.
        conn.executescript(
            f"BEGIN;\n{sql}\n"
            f"INSERT INTO schema_migrations (version, name) VALUES ({version}, '{name}');\n"
            "COMMIT;"
        )
        applied.append(name)
    return applied


def status(conn: sqlite3.Connection) -> dict:
    applied = sorted(applied_versions(conn))
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    pending = [name for version, name, _ in available_migrations() if version not in applied]
    return {"applied": applied, "pending": pending, "tables": tables}
