"""The database is the agent's whole memory; a backup is only worth having if it is a complete,
consistent copy, if old copies are pruned without touching the newest, and if it never lands in
the company's cloud storage."""

import sqlite3
from datetime import date

import pytest

from cryptoagent import backup, research


def test_copy_is_complete_and_checked(conn, tmp_path):
    research.add_claim(conn, topic="bitcoin", subject="BTC", statement="s", status="hypothesis", citations=[],
                       confidence=0.3, created_by="agent")
    result = backup.run(conn, tmp_path / "b", today=date(2026, 11, 1), environ={})
    assert result["integrity"] == "ok" and result["kept"] == ["cryptoagent-2026-11-01.db"]
    copy = sqlite3.connect(result["backup"])
    assert copy.execute("SELECT statement FROM claims").fetchone()[0] == "s"


def test_old_copies_are_pruned(conn, tmp_path):
    for day in range(1, 6):
        result = backup.run(conn, tmp_path, keep=3, today=date(2026, 11, day), environ={})
    assert result["kept"] == ["cryptoagent-2026-11-03.db", "cryptoagent-2026-11-04.db", "cryptoagent-2026-11-05.db"]
    assert result["removed"] == ["cryptoagent-2026-11-02.db"]


def test_refuses_the_work_onedrive(conn, tmp_path):
    work = tmp_path / "OneDrive - Company"
    with pytest.raises(backup.BackupError, match="work OneDrive"):
        backup.run(conn, work / "backups", environ={"OneDriveCommercial": str(work)})
