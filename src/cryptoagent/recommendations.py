"""Recommendation ledger: record structured recommendations and score them mechanically.

A recommendation is bound to the latest stored daily candle (a trigger enforces it) and
has an action, a horizon in days, entry and invalidation conditions, and for a buy a stop
(and optional target) in multiples of ATR14 as of that day.

Once the horizon day's candle is stored, `evaluate` records:
- market_return: close on the horizon day / close on as_of - 1, for every action. For a
  sell/hold/wait/no_trade this is the outcome (a sell or no_trade looks right when it is
  negative); for a buy it is the benchmark.
- for a buy, trade_return: the trade replayed by the backtest engine (entry at the next
  open, the stated stop and target, exit at the horizon close at the latest, default costs).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import pandas as pd

from cryptoagent.predictions import GRACE_DAYS, SMALL_SAMPLE, LedgerError
from cryptoagent.quant import backtest
from cryptoagent.quant import indicators as ind
from cryptoagent.quant.backtest import Costs, Exit
from cryptoagent.quant.series import load_daily, market_id

ACTIONS = ("buy", "sell", "hold", "wait", "no_trade")


def record(conn: sqlite3.Connection, market: str, horizon_days: int, action: str, entry: str, invalidation: str,
           risks: str, limitations: str, created_by: str, stop_atr: float | None = None,
           target_atr: float | None = None, evidence: dict | None = None, prediction_id: int | None = None,
           report_path: str | None = None) -> dict:
    if action not in ACTIONS:
        raise LedgerError(f"action must be one of {', '.join(ACTIONS)}")
    if action == "buy" and stop_atr is None:
        raise LedgerError("a buy needs --stop-atr: every trade states where it is wrong")
    if horizon_days <= 0:
        raise LedgerError("horizon_days must be positive")
    df = load_daily(conn, market)
    if df.empty:
        raise LedgerError("market has no candles")
    atr = float(ind.atr(df, 14).iloc[-1])
    as_of, close = df.index[-1].date().isoformat(), float(df["close"].iloc[-1])
    target_day = (date.fromisoformat(as_of) + timedelta(days=horizon_days)).isoformat()
    with conn:
        rid = conn.execute(
            "INSERT INTO recommendations (created_by, market_id, as_of, as_of_close, horizon_days, target_day, action,"
            " entry, invalidation, stop_atr, target_atr, atr, risks, limitations, evidence, prediction_id, report_path)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (created_by, market_id(conn, market), as_of, close, horizon_days, target_day, action, entry, invalidation,
             stop_atr, target_atr, atr if atr == atr else None, risks, limitations,
             json.dumps(evidence, sort_keys=True) if evidence is not None else None, prediction_id, report_path),
        ).lastrowid
    out = {"id": rid, "market": market, "as_of": as_of, "as_of_close": close, "action": action,
           "horizon_days": horizon_days, "target_day": target_day, "atr14": round(atr, 6)}
    if stop_atr is not None:
        out["stop_level"] = round(close - stop_atr * atr, 6)  # indicative: the replay uses the next open
    if target_atr is not None:
        out["target_level"] = round(close + target_atr * atr, 6)
    return out


def evaluate(conn: sqlite3.Connection) -> dict:
    """Resolve every open recommendation whose horizon day's candle is stored."""
    rows = conn.execute(
        "SELECT r.*, s.name || ':' || m.symbol AS market FROM recommendations r"
        " JOIN markets m ON m.id = r.market_id JOIN sources s ON s.id = m.source_id"
        " LEFT JOIN recommendation_outcomes o ON o.recommendation_id = r.id WHERE o.recommendation_id IS NULL"
    ).fetchall()
    resolved = unresolvable = waiting = 0
    for r in rows:
        df = load_daily(conn, r["market"], r["target_day"])
        if df.empty or df.index[-1].date().isoformat() != r["target_day"]:
            latest = conn.execute("SELECT substr(max(ts), 1, 10) FROM candles WHERE market_id = ? AND timeframe = '1d'",
                                  (r["market_id"],)).fetchone()[0]
            if latest and date.fromisoformat(latest) > date.fromisoformat(r["target_day"]) + timedelta(days=GRACE_DAYS):
                with conn:
                    conn.execute("INSERT INTO recommendation_outcomes (recommendation_id, status, note)"
                                 " VALUES (?, 'unresolvable', ?)", (r["id"], f"no candle for {r['target_day']}"))
                unresolvable += 1
            else:
                waiting += 1
            continue
        market_return = float(df["close"].iloc[-1]) / r["as_of_close"] - 1
        trade = {}
        if r["action"] == "buy":
            window = df[df.index >= pd.Timestamp(r["as_of"])]
            signal = pd.Series(False, index=window.index)
            signal.iloc[0] = True
            trades, _ = backtest.simulate(window, signal, Exit(r["horizon_days"], r["stop_atr"], r["target_atr"]),
                                          Costs(), atr=pd.Series(r["atr"], index=window.index))
            if trades:
                t = trades[0]
                trade = {"trade_return": t["return"], "exit_reason": t["reason"], "entry_price": t["entry"],
                         "exit_price": t["exit"]}
        with conn:
            conn.execute(
                "INSERT INTO recommendation_outcomes (recommendation_id, status, market_return, trade_return, exit_reason,"
                " entry_price, exit_price) VALUES (?, 'resolved', ?, ?, ?, ?, ?)",
                (r["id"], market_return, trade.get("trade_return"), trade.get("exit_reason"), trade.get("entry_price"),
                 trade.get("exit_price")),
            )
        resolved += 1
    return {"resolved": resolved, "unresolvable": unresolvable, "waiting": waiting}


def list_recommendations(conn: sqlite3.Connection, status: str | None = None, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT r.id, s.name || ':' || m.symbol AS market, r.created_at, r.created_by, r.as_of, r.as_of_close,"
        " r.horizon_days, r.target_day, r.action, r.entry, r.invalidation, r.stop_atr, r.target_atr, r.atr, r.risks,"
        " r.limitations, r.prediction_id, r.report_path, coalesce(o.status, 'open') AS status, o.market_return,"
        " o.trade_return, o.exit_reason FROM recommendations r JOIN markets m ON m.id = r.market_id"
        " JOIN sources s ON s.id = m.source_id LEFT JOIN recommendation_outcomes o ON o.recommendation_id = r.id"
        " WHERE (? IS NULL OR coalesce(o.status, 'open') = ?) ORDER BY r.id DESC LIMIT ?",
        (status, status, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def score(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT r.action, r.horizon_days, o.status, o.market_return, o.trade_return FROM recommendations r"
        " LEFT JOIN recommendation_outcomes o ON o.recommendation_id = r.id"
    ).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["action", "horizon_days", "status", "market_return",
                                                         "trade_return"])
    out = []
    for action in ACTIONS:
        g = df[df["action"] == action]
        if g.empty:
            continue
        done = g[g["status"] == "resolved"]
        entry = {"action": action, "recommendations": len(g), "open": int(g["status"].isna().sum()),
                 "resolved": len(done)}
        if len(done):
            entry["mean_market_return"] = round(float(done["market_return"].mean()), 6)
            entry["share_market_up"] = round(float((done["market_return"] > 0).mean()), 4)
            if action == "buy":
                trades = done["trade_return"].dropna()
                entry["mean_trade_return"] = round(float(trades.mean()), 6) if len(trades) else None
                entry["share_trades_won"] = round(float((trades > 0).mean()), 4) if len(trades) else None
            if len(done) < SMALL_SAMPLE:
                entry["warning"] = f"only {len(done)} resolved: too few to judge (need {SMALL_SAMPLE}+)"
        out.append(entry)
    return out
