"""The knowledge base is how research accumulates across sessions. It is only trustworthy if a
"fact" always points at something checkable (a page read, a recorded trial or prediction), a
guess is labelled as a guess with a confidence, and nothing is silently rewritten: corrections
supersede, and the history stays."""

import sqlite3

import pytest

from cryptoagent import research
from cryptoagent.cli import main
from cryptoagent.research import Citation, ResearchError

URL = Citation("url", "https://example.org/report", "fees fell 40% after the upgrade")


def add(conn, **kw):
    base = dict(topic="fundamentals", subject="eth", statement="ETH fees fell after the upgrade", status="fact",
                citations=[URL], created_by="agent")
    return research.add_claim(conn, **(base | kw))


def test_fact_needs_a_citation_and_hypothesis_needs_confidence(conn):
    with pytest.raises(ResearchError, match="at least one citation"):
        add(conn, citations=[])
    with pytest.raises(ResearchError, match="confidence"):
        add(conn, status="hypothesis", citations=[])
    assert add(conn, status="hypothesis", citations=[], confidence=0.4) > 0
    claim = research.list_claims(conn, subject="ETH")[0]
    assert claim["subject"] == "ETH"  # normalised


def test_citations_must_resolve(conn):
    with pytest.raises(ResearchError, match="trial 999 does not exist"):
        add(conn, citations=[Citation("trial", "999")])
    with pytest.raises(ResearchError, match="longer than 25 words"):
        add(conn, citations=[Citation("url", "https://x.org", " ".join(["word"] * 26))])
    with pytest.raises(ResearchError, match="must look like"):
        research.parse_citation("observation:3")


def test_corrections_supersede_and_history_is_kept(conn):
    old = add(conn)
    new = add(conn, statement="ETH fees fell 35%, not 40%", supersedes_id=old)
    assert [c["id"] for c in research.list_claims(conn)] == [new]
    assert [c["id"] for c in research.list_claims(conn, include_superseded=True)] == [old, new]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE claims SET statement = 'edited' WHERE id = ?", (old,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM claim_citations")


def test_review_due_filter(conn):
    add(conn, review_after="2000-01-01")
    add(conn, statement="later", review_after="2999-01-01")
    assert [c["statement"] for c in research.list_claims(conn, due_for_review=True)] == [
        "ETH fees fell after the upgrade"]


def test_cli_rejects_uncited_fact(tmp_path, capsys):
    path = str(tmp_path / "t.db")
    main(["--db", path, "init-db"])
    assert main(["--db", path, "note", "add", "--topic", "bitcoin", "--subject", "BTC", "--status", "fact",
                 "--text", "x"]) == 2
    assert "at least one citation" in capsys.readouterr().err
