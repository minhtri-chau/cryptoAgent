"""Nightly copy of the database: everything the project knows (candles, trials, predictions,
recommendations, claims, journal) lives in one SQLite file, so it needs a copy elsewhere.

- SQLite's online backup API makes a consistent copy even while the database is open
- each copy is integrity-checked before older copies are pruned
- destinations under the work OneDrive (OneDriveCommercial) are refused: this is a personal
  project and company services are off limits (CLAUDE.md)
- the copy contains DefiLlama data held under a personal-use accepted risk: keep backups private
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

DEFAULT_DIR = Path.home() / "Backups" / "cryptoAgent"
DEFAULT_KEEP = 14
PREFIX = "cryptoagent-"


class BackupError(ValueError):
    pass


def _is_under(path: Path, parent: str | None) -> bool:
    if not parent:
        return False
    try:
        path.resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def run(conn: sqlite3.Connection, directory: Path, keep: int = DEFAULT_KEEP, today: date | None = None,
        environ: dict | None = None) -> dict:
    env = os.environ if environ is None else environ
    if _is_under(directory, env.get("OneDriveCommercial")):
        raise BackupError(f"{directory} is in the work OneDrive; choose a personal location (CRYPTOAGENT_BACKUP_DIR)")
    if keep < 1:
        raise BackupError("keep must be at least 1")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{PREFIX}{(today or date.today()).isoformat()}.db"
    partial = target.with_suffix(".partial")
    copy = sqlite3.connect(partial)
    try:
        conn.backup(copy)
        status = copy.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        copy.close()
    if status != "ok":
        partial.unlink(missing_ok=True)
        raise BackupError(f"backup failed its integrity check: {status}")
    partial.replace(target)  # a finished, checked copy replaces any earlier copy from the same day
    copies = sorted(directory.glob(f"{PREFIX}????-??-??.db"))
    removed = [p.name for p in copies[:-keep]]
    for name in removed:
        (directory / name).unlink()
    return {"backup": str(target), "bytes": target.stat().st_size, "integrity": status,
            "kept": [p.name for p in sorted(directory.glob(f"{PREFIX}????-??-??.db"))], "removed": removed}
