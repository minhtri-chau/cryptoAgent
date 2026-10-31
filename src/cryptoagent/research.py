"""Persistent research knowledge: cited facts and labelled hypotheses about the market and coins.

Claude adds findings through the CLI (`note add`), never by editing rows. A fact must cite at
least one source: a web page it read, a recorded trial or prediction, or another claim. A
hypothesis must carry a confidence. Claims are append-only; corrections supersede.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

from cryptoagent import privacy

TOPICS = (
    "market_structure", "bitcoin", "altcoins", "fundamentals", "tokenomics", "regulation", "macro",
    "exchanges", "strategy", "events", "data_sources",
)
CITATION_KINDS = {"url": "url", "trial": "trial", "prediction": "prediction", "claim": "claim"}
MAX_EXCERPT_WORDS = 25
_TABLES = {"trial": "trials", "prediction": "predictions", "claim": "claims"}


class ResearchError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class Citation:
    kind: str  # 'url' | 'trial' | 'prediction' | 'claim'
    ref: str
    excerpt: str | None = None


def parse_citation(text: str) -> Citation:
    """Parse 'url:https://...', 'trial:12', 'prediction:17' or 'claim:3'."""
    prefix, _, ref = text.partition(":")
    kind = CITATION_KINDS.get(prefix)
    if not kind or not ref:
        raise ResearchError([f"citation {text!r} must look like url:https://..., trial:<id>, prediction:<id> or "
                             "claim:<id>"])
    return Citation(kind, ref)


def _check_citation(conn: sqlite3.Connection, c: Citation) -> list[str]:
    if c.excerpt and len(c.excerpt.split()) > MAX_EXCERPT_WORDS:
        return [f"excerpt for {c.ref} is longer than {MAX_EXCERPT_WORDS} words"]
    if c.kind == "url":
        return [] if c.ref.startswith(("http://", "https://")) else [f"url {c.ref!r} must start with http(s)://"]
    if not c.ref.isdigit() or not conn.execute(f"SELECT 1 FROM {_TABLES[c.kind]} WHERE id = ?",
                                               (int(c.ref),)).fetchone():
        return [f"{c.kind} {c.ref} does not exist"]
    return []


def add_claim(conn: sqlite3.Connection, *, topic: str, subject: str, statement: str, status: str,
              citations: list[Citation], confidence: float | None = None, as_of: str | None = None,
              review_after: str | None = None, created_by: str = "user", supersedes_id: int | None = None) -> int:
    privacy.check(statement, *[c.excerpt for c in citations])
    errors = []
    if topic not in TOPICS:
        errors.append(f"topic must be one of {', '.join(TOPICS)}")
    if not subject.strip():
        errors.append("subject is required: MARKET or a coin symbol such as ETH")
    if not statement.strip():
        errors.append("statement is required")
    if status == "fact":
        if not citations:
            errors.append("a fact needs at least one citation; record it as a hypothesis otherwise")
        if confidence is not None:
            errors.append("facts take no confidence; only hypotheses do")
    elif status == "hypothesis":
        if confidence is None or not 0 <= confidence <= 1:
            errors.append("a hypothesis needs a confidence between 0 and 1")
    else:
        errors.append("status must be 'fact' or 'hypothesis'")
    if created_by not in ("agent", "user"):
        errors.append("created_by must be agent or user")
    for when in (as_of, review_after):
        if when:
            try:
                date.fromisoformat(when)
            except ValueError:
                errors.append(f"date {when!r} must be YYYY-MM-DD")
    if supersedes_id is not None and not conn.execute("SELECT 1 FROM claims WHERE id = ?", (supersedes_id,)).fetchone():
        errors.append(f"claim {supersedes_id} to supersede does not exist")
    for c in citations:
        errors.extend(_check_citation(conn, c))
    if errors:
        raise ResearchError(errors)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        claim_id = conn.execute(
            "INSERT INTO claims (topic, subject, statement, status, confidence, as_of, review_after, created_by,"
            " supersedes_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (topic, subject.strip().upper(), statement.strip(), status, confidence, as_of or now[:10], review_after,
             created_by, supersedes_id),
        ).lastrowid
        conn.executemany(
            "INSERT INTO claim_citations (claim_id, kind, ref, excerpt, accessed_at) VALUES (?, ?, ?, ?, ?)",
            [(claim_id, c.kind, c.ref, c.excerpt, now if c.kind == "url" else None) for c in citations],
        )
    return claim_id


def list_claims(conn: sqlite3.Connection, topic: str | None = None, subject: str | None = None,
                status: str | None = None, include_superseded: bool = False, due_for_review: bool = False) -> list[dict]:
    table = "claims" if include_superseded else "current_claims"
    sql, args = f"SELECT * FROM {table} WHERE 1 = 1", []
    for column, value in (("topic", topic), ("subject", subject.upper() if subject else None), ("status", status)):
        if value:
            sql, args = sql + f" AND {column} = ?", args + [value]
    if due_for_review:
        sql += " AND review_after IS NOT NULL AND review_after <= date('now')"
    claims = [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]
    for claim in claims:
        claim["citations"] = [dict(r) for r in conn.execute(
            "SELECT kind, ref, excerpt, accessed_at FROM claim_citations WHERE claim_id = ? ORDER BY id",
            (claim["id"],))]
    return claims
