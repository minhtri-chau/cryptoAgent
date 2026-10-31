"""Prediction ledger: record, evaluate and score probabilistic direction predictions.

A prediction says P(close on as_of + h days > close on as_of). The reference candle is
always the latest stored complete candle (a trigger enforces this), so nothing can be
back-dated. A close exactly equal to the reference counts as not up.

Baseline models are deterministic and recorded daily, starting the forward-test clock:
- base_rate_v1: share of all past h-day windows that ended up, using only windows that
  ended on or before as_of.
- base_rate_trend_v1: the same, restricted to windows that started in the current
  regime-v1 trend label.
Claude's own predictions are recorded with source 'judgment' and scored the same way.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import numpy as np
import pandas as pd

from cryptoagent import privacy
from cryptoagent.quant import analogs, regime
from cryptoagent.quant.series import load_daily, market_id

BASELINE_HORIZONS = (7, 30, 90, 365)
ANALOG_K = {30: 15, 90: 10}  # analog_v1 forward predictions: horizon -> neighbours (fewer separate episodes fit 90 days)
MIN_WINDOWS = 30          # fewer past windows than this and a base rate is not recorded
GRACE_DAYS = 7            # a missing target candle is waited for this long, then unresolvable
SMALL_SAMPLE = 30         # scorecard warning below this many approximately independent outcomes


class LedgerError(ValueError):
    pass


def _latest(conn, mid: int) -> tuple[str, float]:
    row = conn.execute(
        "SELECT substr(ts, 1, 10), close FROM candles WHERE market_id = ? AND timeframe = '1d' ORDER BY ts DESC LIMIT 1",
        (mid,),
    ).fetchone()
    if row is None:
        raise LedgerError("market has no candles")
    return row[0], row[1]


def record(conn: sqlite3.Connection, market: str, horizon_days: int, prob_up: float, source: str, model: str,
           rationale: str | None = None, evidence: dict | None = None) -> int:
    privacy.check(rationale, evidence)
    if not 0 <= prob_up <= 1:
        raise LedgerError("prob_up must be between 0 and 1")
    if horizon_days <= 0:
        raise LedgerError("horizon_days must be positive")
    mid = market_id(conn, market)
    as_of, close = _latest(conn, mid)
    target = (date.fromisoformat(as_of) + timedelta(days=horizon_days)).isoformat()
    with conn:
        return conn.execute(
            "INSERT INTO predictions (market_id, as_of, as_of_close, horizon_days, target_day, prob_up, source, model,"
            " rationale, evidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, as_of, close, horizon_days, target, prob_up, source, model, rationale,
             json.dumps(evidence, sort_keys=True) if evidence is not None else None),
        ).lastrowid


def base_rates(df: pd.DataFrame, horizon: int) -> dict[str, dict]:
    """Base-rate probabilities for an h-day rise from the last day of `df`, using only `df`."""
    close = df["close"]
    future = close.shift(-horizon, freq="D").reindex(close.index)  # close h calendar days later, if stored
    ups = (future > close)[future.notna()]  # windows that both start and end within df
    out = {}
    n = len(ups)
    if n >= MIN_WINDOWS:
        out["base_rate_v1"] = {"prob_up": float(ups.mean()), "windows": n, "approx_independent": n // horizon,
                               "first_window": ups.index[0].date().isoformat()}
    trend = regime.classify(df)["trend"]
    current = trend.iloc[-1]
    if isinstance(current, str):
        same = ups[trend.reindex(ups.index) == current]
        if len(same) >= MIN_WINDOWS:
            out["base_rate_trend_v1"] = {"prob_up": float(same.mean()), "windows": len(same),
                                         "approx_independent": len(same) // horizon, "trend": current,
                                         "regime_version": regime.VERSION}
    return out


def record_baselines(conn: sqlite3.Connection, market: str, horizons=BASELINE_HORIZONS) -> list[dict]:
    """Record today's baseline predictions; skips any already recorded for the latest candle."""
    df = load_daily(conn, market)
    mid = market_id(conn, market)
    as_of = df.index[-1].date().isoformat()
    made = []
    for h in horizons:
        for model, info in base_rates(df, h).items():
            exists = conn.execute(
                "SELECT 1 FROM predictions WHERE market_id = ? AND as_of = ? AND horizon_days = ? AND source = 'model'"
                " AND model = ?", (mid, as_of, h, model)).fetchone()
            if exists:
                continue
            pid = record(conn, market, h, info["prob_up"], "model", model, evidence=info)
            made.append({"id": pid, "model": model, "horizon_days": h, "prob_up": round(info["prob_up"], 4),
                         "windows": info["windows"]})
    for h, k in ANALOG_K.items():
        if h not in horizons or conn.execute(
                "SELECT 1 FROM predictions WHERE market_id = ? AND as_of = ? AND horizon_days = ? AND source = 'model'"
                " AND model = 'analog_v1'", (mid, as_of, h)).fetchone():
            continue
        try:
            found = analogs.search(df, h, k)
        except analogs.AnalogError:
            continue  # too little history: no analogue prediction rather than a weak one
        evidence = {"version": analogs.VERSION, "k": k, "effective_n": found["effective_n"],
                    "neighbours": [n["day"] for n in found["neighbours"]],
                    "unconditional_share_up": found["unconditional"]["share_up"]}
        prob = found["analogues"]["share_up"]
        pid = record(conn, market, h, prob, "model", "analog_v1", evidence=evidence)
        made.append({"id": pid, "model": "analog_v1", "horizon_days": h, "prob_up": prob,
                     "windows": found["effective_n"]})
    return made


def evaluate(conn: sqlite3.Connection) -> dict:
    """Resolve every open prediction whose target day's candle is stored."""
    open_rows = conn.execute(
        "SELECT p.id, p.market_id, p.target_day, p.as_of_close, p.prob_up FROM predictions p"
        " LEFT JOIN outcomes o ON o.prediction_id = p.id WHERE o.prediction_id IS NULL"
    ).fetchall()
    resolved = unresolvable = waiting = 0
    with conn:
        for pid, mid, target, ref_close, prob in open_rows:
            row = conn.execute("SELECT close FROM candles WHERE market_id = ? AND timeframe = '1d' AND ts = ?",
                               (mid, f"{target}T00:00:00Z")).fetchone()
            if row is not None:
                up = int(row[0] > ref_close)
                conn.execute(
                    "INSERT INTO outcomes (prediction_id, status, target_close, realized_return, up, brier)"
                    " VALUES (?, 'resolved', ?, ?, ?, ?)",
                    (pid, row[0], row[0] / ref_close - 1, up, (prob - up) ** 2),
                )
                resolved += 1
                continue
            latest = conn.execute("SELECT substr(max(ts), 1, 10) FROM candles WHERE market_id = ? AND timeframe = '1d'",
                                  (mid,)).fetchone()[0]
            if latest and date.fromisoformat(latest) > date.fromisoformat(target) + timedelta(days=GRACE_DAYS):
                conn.execute("INSERT INTO outcomes (prediction_id, status, note) VALUES (?, 'unresolvable', ?)",
                             (pid, f"no candle for {target}"))
                unresolvable += 1
            else:
                waiting += 1
    return {"resolved": resolved, "unresolvable": unresolvable, "waiting": waiting}


def _approx_independent(as_of_days: list[str], horizon: int) -> int:
    """Count predictions whose windows don't overlap, taken greedily in date order."""
    count, next_free = 0, None
    for d in sorted(as_of_days):
        day = date.fromisoformat(d)
        if next_free is None or day >= next_free:
            count += 1
            next_free = day + timedelta(days=horizon)
    return count


def scorecard(conn: sqlite3.Connection, market: str | None = None) -> list[dict]:
    rows = conn.execute(
        "SELECT p.id, s.name || ':' || m.symbol AS market, p.as_of, p.horizon_days, p.source, p.model, p.prob_up,"
        " o.status, o.up, o.brier FROM predictions p JOIN markets m ON m.id = p.market_id"
        " JOIN sources s ON s.id = m.source_id LEFT JOIN outcomes o ON o.prediction_id = p.id"
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows], columns=["id", "market", "as_of", "horizon_days", "source", "model",
                                                        "prob_up", "status", "up", "brier"])
    if market:
        df = df[df["market"] == market]
    base = df[(df["model"] == "base_rate_v1") & (df["status"] == "resolved")].set_index(
        ["market", "as_of", "horizon_days"])["brier"]
    out = []
    for (mkt, source, model, h), g in df.groupby(["market", "source", "model", "horizon_days"], sort=True):
        done = g[g["status"] == "resolved"]
        entry = {"market": mkt, "source": source, "model": model, "horizon_days": int(h), "predictions": len(g),
                 "open": int(g["status"].isna().sum()), "unresolvable": int((g["status"] == "unresolvable").sum()),
                 "resolved": len(done)}
        if len(done):
            independent = _approx_independent(list(done["as_of"]), int(h))
            calls = done[done["prob_up"] != 0.5]
            entry.update({
                "approx_independent": independent,
                "brier": round(float(done["brier"].mean()), 6),
                "mean_prob_up": round(float(done["prob_up"].mean()), 4),
                "observed_up": round(float(done["up"].mean()), 4),
                "hit_rate": round(float(((calls["prob_up"] > 0.5) == (calls["up"] == 1)).mean()), 4) if len(calls)
                else None,
                "calibration": [
                    {"bin": f"{lo:.1f}-{lo + 0.1:.1f}", "n": len(b), "mean_prob_up": round(float(b["prob_up"].mean()), 4),
                     "observed_up": round(float(b["up"].mean()), 4)}
                    for lo in np.round(np.arange(0, 1, 0.1), 1)
                    if len(b := done[(done["prob_up"] >= lo) & ((done["prob_up"] < lo + 0.1) | (lo == 0.9))])
                ],
            })
            if model != "base_rate_v1":
                keys = list(zip(done["market"], done["as_of"], done["horizon_days"]))
                paired = [(b, base.get(k)) for b, k in zip(done["brier"], keys) if k in base.index]
                if paired:
                    ours, theirs = (np.mean([p[0] for p in paired]), np.mean([p[1] for p in paired]))
                    entry["brier_skill_vs_base_rate"] = round(float(1 - ours / theirs), 6) if theirs > 0 else None
                    entry["paired_with_base_rate"] = len(paired)
            if independent < SMALL_SAMPLE:
                entry["warning"] = (f"only ~{independent} non-overlapping outcomes; "
                                    f"too few to conclude anything (need {SMALL_SAMPLE}+)")
        out.append(entry)
    return out


def list_predictions(conn: sqlite3.Connection, status: str | None = None, source: str | None = None,
                     limit: int = 50, market: str | None = None) -> list[dict]:
    rows = conn.execute(
        "SELECT p.id, s.name || ':' || m.symbol AS market, p.created_at, p.as_of, p.as_of_close, p.horizon_days,"
        " p.target_day, p.prob_up, p.source, p.model, p.rationale, coalesce(o.status, 'open') AS status,"
        " o.target_close, o.realized_return, o.up, o.brier"
        " FROM predictions p JOIN markets m ON m.id = p.market_id JOIN sources s ON s.id = m.source_id"
        " LEFT JOIN outcomes o ON o.prediction_id = p.id"
        " WHERE (? IS NULL OR coalesce(o.status, 'open') = ?) AND (? IS NULL OR p.source = ?)"
        " AND (? IS NULL OR s.name || ':' || m.symbol = ?) ORDER BY p.id DESC LIMIT ?",
        (status, status, source, source, market, market, limit),
    ).fetchall()
    return [dict(r) for r in rows]
