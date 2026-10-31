"""What changed since the last session, as facts, so each daily conversation starts from the record
rather than from memory. Read-only, apart from `add_entry` (the append-only journal)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from cryptoagent import predictions, privacy, recommendations, research, strategies

DEFAULT_LOOKBACK_DAYS = 7


def add_entry(conn: sqlite3.Connection, kind: str, author: str, summary: str, report_path: str | None = None,
              evidence: dict | None = None) -> int:
    privacy.check(summary, evidence)
    with conn:
        return conn.execute(
            "INSERT INTO journal (kind, author, summary, report_path, evidence) VALUES (?, ?, ?, ?, ?)",
            (kind, author, summary.strip(), report_path, json.dumps(evidence, sort_keys=True) if evidence else None),
        ).lastrowid


def last_briefing(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM journal WHERE kind = 'briefing' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def digest(conn: sqlite3.Connection, since: str | None = None, ahead_days: int = 7) -> dict:
    """Everything recorded since `since` (default: the last briefing, else 7 days ago)."""
    previous = last_briefing(conn)
    if since is None:
        since = previous["created_at"] if previous else \
            (datetime.now(timezone.utc) - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif len(since) == 10:
        since += "T00:00:00Z"
    today = date.today()
    horizon = (today + timedelta(days=ahead_days)).isoformat()

    latest = {r[0]: r[1] for r in conn.execute(
        "SELECT s.name, substr(max(c.ts), 1, 10) FROM candles c JOIN markets m ON m.id = c.market_id"
        " JOIN sources s ON s.id = m.source_id WHERE c.timeframe = '1d' GROUP BY s.name")}
    failed_runs = [dict(r) for r in conn.execute(
        "SELECT r.id, s.name AS source, r.status, r.started_at, r.error FROM ingest_runs r JOIN sources s ON s.id = r.source_id"
        " WHERE r.started_at >= ? AND r.status <> 'succeeded' ORDER BY r.id", (since,))]
    new_issues = dict(conn.execute("SELECT check_name, count(*) FROM quality_issues WHERE detected_at >= ? GROUP BY 1",
                                   (since,)).fetchall())

    resolved_predictions = [dict(r) for r in conn.execute(
        "SELECT p.id, s.name || ':' || m.symbol AS market, p.source, p.model, p.horizon_days, p.as_of, p.target_day,"
        " p.prob_up, o.status, o.up, round(o.brier, 4) AS brier, round(o.realized_return, 4) AS realized_return"
        " FROM outcomes o JOIN predictions p ON p.id = o.prediction_id JOIN markets m ON m.id = p.market_id"
        " JOIN sources s ON s.id = m.source_id WHERE o.resolved_at >= ? ORDER BY p.source DESC, p.id", (since,))]
    resolved_recommendations = [dict(r) for r in conn.execute(
        "SELECT r.id, s.name || ':' || m.symbol AS market, r.action, r.horizon_days, r.as_of, r.target_day,"
        " o.status, round(o.market_return, 4) AS market_return, round(o.trade_return, 4) AS trade_return,"
        " o.exit_reason FROM recommendation_outcomes o JOIN recommendations r ON r.id = o.recommendation_id"
        " JOIN markets m ON m.id = r.market_id JOIN sources s ON s.id = m.source_id WHERE o.resolved_at >= ?"
        " ORDER BY r.id", (since,))]
    due_soon = {
        "predictions": [dict(r) for r in conn.execute(
            "SELECT p.id, s.name || ':' || m.symbol AS market, p.source, p.model, p.target_day, p.prob_up"
            " FROM predictions p JOIN markets m ON m.id = p.market_id JOIN sources s ON s.id = m.source_id"
            " LEFT JOIN outcomes o ON o.prediction_id = p.id WHERE o.prediction_id IS NULL AND p.target_day <= ?"
            " AND p.source = 'judgment' ORDER BY p.target_day", (horizon,))],
        "recommendations": [dict(r) for r in conn.execute(
            "SELECT r.id, s.name || ':' || m.symbol AS market, r.action, r.target_day FROM recommendations r"
            " JOIN markets m ON m.id = r.market_id JOIN sources s ON s.id = m.source_id"
            " LEFT JOIN recommendation_outcomes o ON o.recommendation_id = r.id WHERE o.recommendation_id IS NULL"
            " AND r.target_day <= ? ORDER BY r.target_day", (horizon,))],
    }
    open_counts = {
        "predictions": conn.execute("SELECT count(*) FROM predictions p LEFT JOIN outcomes o ON o.prediction_id = p.id"
                                    " WHERE o.prediction_id IS NULL").fetchone()[0],
        "recommendations": conn.execute("SELECT count(*) FROM recommendations r LEFT JOIN recommendation_outcomes o"
                                        " ON o.recommendation_id = r.id WHERE o.recommendation_id IS NULL").fetchone()[0],
    }
    strategy_events = [dict(r) for r in conn.execute(
        "SELECT s.name AS strategy, v.version, e.event, e.decided_by, e.created_at FROM strategy_events e"
        " JOIN strategy_versions v ON v.id = e.strategy_version_id JOIN strategies s ON s.id = v.strategy_id"
        " WHERE e.created_at >= ? ORDER BY e.id", (since,))]
    paper = {"opened": conn.execute("SELECT count(*) FROM paper_trades WHERE created_at >= ?", (since,)).fetchone()[0],
             "resolved": conn.execute("SELECT count(*) FROM paper_outcomes WHERE resolved_at >= ?",
                                      (since,)).fetchone()[0]}
    trials_since = [dict(r) for r in conn.execute(
        "SELECT family, count(*) AS trials FROM trials WHERE created_at >= ? GROUP BY family ORDER BY family", (since,))]
    judgment = [c for c in predictions.scorecard(conn) if c["source"] == "judgment" and c["resolved"]]

    return {
        "since": since, "today": today.isoformat(),
        "last_briefing": previous and {k: previous[k] for k in ("id", "created_at", "summary", "report_path")},
        "data": {"latest_daily_candle": latest, "failed_runs_since": failed_runs, "new_quality_issues": new_issues},
        "resolved_since": {"predictions": resolved_predictions, "recommendations": resolved_recommendations},
        "due_within_days": ahead_days, "due_soon": due_soon, "open": open_counts,
        "track_record": {"judgment_predictions": judgment, "recommendations": recommendations.score(conn)},
        "strategies": {"events_since": strategy_events, "paper_trades": paper,
                       "approved_signals": strategies.approved_signals(conn)},
        "trials_since": trials_since,
        "claims_due_for_review": [{k: c[k] for k in ("id", "subject", "topic", "statement", "review_after")}
                                  for c in research.list_claims(conn, due_for_review=True)],
    }


def list_entries(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,))]
