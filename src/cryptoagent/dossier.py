"""Everything the project knows about one coin, in one place, so research builds on itself instead
of being repeated: market facts, universe membership, fundamentals, supply and valuation snapshots,
saved claims, trials, the agent's past calls and outcomes, outside commentators' calls, and the
research reports that mention it. Read-only."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from cryptoagent import fundamentals, research
from cryptoagent.quant import indicators as ind
from cryptoagent.quant import universe
from cryptoagent.quant.series import UnknownMarket, load_daily

REPORTS = Path("reports") / "research"


def _market_facts(conn, market: str, symbol: str) -> dict | None:
    try:
        df = load_daily(conn, market)
    except UnknownMarket:
        return None
    if df.empty:
        return {"market": market, "candles": 0}
    last = ind.compute_all(df).iloc[-1]
    member, reason, facts = universe.membership(df, df.index[-1], symbol)
    status = conn.execute(
        "SELECT h.status FROM market_status_history h JOIN markets m ON m.id = h.market_id JOIN sources s"
        " ON s.id = m.source_id WHERE s.name || ':' || m.symbol = ? ORDER BY h.id DESC LIMIT 1", (market,)).fetchone()

    def pct(v):
        return None if v != v else round(float(v), 6)

    return {"market": market, "status": status[0] if status else None, "first_day": df.index[0].date().isoformat(),
            "last_day": df.index[-1].date().isoformat(), "close": float(last["close"]),
            "return_30d": pct(last["roc30"]), "return_90d": pct(last["roc90"]), "return_365d": pct(last["roc365"]),
            "drawdown_from_high": pct(last["drawdown_ath"]), "ema200_deviation": pct(last["ema200_dev"]),
            "universe_member": member, "universe_reason": reason,
            "median_dollar_volume_30d": facts.get("median_dollar_volume")}


def build(conn: sqlite3.Connection, symbol: str) -> dict:
    symbol = symbol.upper()
    like = f"%:{symbol}/USD"
    markets = [r[0] for r in conn.execute(
        "SELECT s.name || ':' || m.symbol FROM markets m JOIN sources s ON s.id = m.source_id WHERE m.symbol = ?"
        " ORDER BY s.name", (f"{symbol}/USD",))]
    if not markets and not conn.execute("SELECT 1 FROM claims WHERE subject = ?", (symbol,)).fetchone():
        return {"symbol": symbol, "known": False, "note": "no market data or research stored for this symbol"}
    supply = None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'token_snapshots'").fetchone():
        row = conn.execute("SELECT * FROM token_snapshots WHERE symbol = ? ORDER BY day DESC LIMIT 1",
                           (symbol,)).fetchone()
        supply = dict(row) if row else None
    trials = [dict(r) for r in conn.execute(
        "SELECT id, family, period, trades, round(mean_return, 4) AS mean_return, json_extract(spec, '$.pattern')"
        " AS pattern, json_extract(spec, '$.method') AS method, created_at FROM trials WHERE market LIKE ?"
        " ORDER BY id", (like,))]
    recs = [dict(r) for r in conn.execute(
        "SELECT r.id, r.as_of, r.action, r.horizon_days, r.target_day, coalesce(o.status, 'open') AS status,"
        " round(o.market_return, 4) AS market_return, round(o.trade_return, 4) AS trade_return, r.report_path"
        " FROM recommendations r JOIN markets m ON m.id = r.market_id LEFT JOIN recommendation_outcomes o"
        " ON o.recommendation_id = r.id WHERE m.symbol = ? ORDER BY r.id", (f"{symbol}/USD",))]
    preds = [dict(r) for r in conn.execute(
        "SELECT p.id, p.as_of, p.horizon_days, p.prob_up, p.target_day, coalesce(o.status, 'open') AS status, o.up,"
        " round(o.brier, 4) AS brier FROM predictions p JOIN markets m ON m.id = p.market_id"
        " LEFT JOIN outcomes o ON o.prediction_id = p.id WHERE m.symbol = ? AND p.source = 'judgment' ORDER BY p.id",
        (f"{symbol}/USD",))]
    calls = [dict(r) for r in conn.execute(
        "SELECT c.id, c.creator, c.call_type, c.target, c.published_on, c.deadline, coalesce(o.status, 'open')"
        " AS status, o.hit FROM external_calls c JOIN markets m ON m.id = c.market_id"
        " LEFT JOIN external_outcomes o ON o.call_id = c.id WHERE m.symbol = ? ORDER BY c.id", (f"{symbol}/USD",))]
    reports = sorted(str(p).replace("\\", "/") for p in REPORTS.glob(f"*{symbol}*.md")) if REPORTS.exists() else []
    claims = research.list_claims(conn, subject=symbol)
    return {
        "symbol": symbol, "known": True,
        "markets": [m for m in (_market_facts(conn, mk, symbol) for mk in markets) if m],
        "fundamentals": fundamentals.for_symbol(conn, symbol),
        "token_snapshot": supply,
        "claims": {"facts": [c for c in claims if c["status"] == "fact"],
                   "hypotheses": [c for c in claims if c["status"] == "hypothesis"],
                   "due_for_review": [c["id"] for c in research.list_claims(conn, subject=symbol, due_for_review=True)]},
        "trials": trials,
        "agent_recommendations": recs,
        "agent_judgment_predictions": preds,
        "outside_calls": calls,
        "reports": reports,
        "notes": ["fundamentals and token snapshots exist only from when collection started; absence is not a "
                  "negative signal", "claims marked due_for_review may be stale: re-check before relying on them"],
    }
