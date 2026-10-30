"""The contract every data provider implements.

A collector turns one provider's data into `ingest_runs` plus its data rows (candles or
reference prices). The rest of the system only reads those tables, so providers can be
added, replaced or removed without touching analysis code.
"""

from __future__ import annotations

import sqlite3
from typing import Protocol

from cryptoagent.config import Settings


class Collector(Protocol):
    name: str
    source_kind: str  # 'exchange' | 'reference'
    description: str

    def configured(self, settings: Settings) -> bool:
        """True when everything the collector needs (keys, files) is available."""

    def collect(self, conn: sqlite3.Connection, settings: Settings, **options) -> dict:
        """Run one collection and return a JSON-serialisable summary including run_id."""
