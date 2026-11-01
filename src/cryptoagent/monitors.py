"""Forward evidence for registry hypotheses, and the conditional baseline their tests need.

- `conditional_baseline`: forward returns over every day that meets some conditions (e.g. all
  downtrend days) against all days, on the train or validation period only. The sealed test
  period is never read. It answers "does the signal add anything beyond the condition it lives in".
- Monitors (migration 011): when a hypothesis's signal is active on the latest candle, record a
  model prediction 'hyp_<id>' with `hyp-monitor-v1`:
      P(up) = (k + M x b) / (n + M)
  where k of n past signal windows ended up, b is the share of all past windows that ended up,
  and M = 20 is the prior weight. With a handful of past signals the estimate stays near the base
  rate instead of trusting 3 cases. Each prediction is linked to the hypothesis as forward
  evidence (direction 'neutral' until it resolves); the scorecard compares it with base_rate_v1.
Only signals on the latest stored candle count: a signal that fired before a monitor existed is
never back-recorded.
"""

from __future__ import annotations

import json
import sqlite3

import pandas as pd

from cryptoagent import hypotheses, predictions, trials
from cryptoagent.quant import backtest, patterns
from cryptoagent.quant.patterns import SpecError
from cryptoagent.quant.series import load_daily

VERSION = "hyp-monitor-v1"
PRIOR_WEIGHT = 20
BASELINE_PERIODS = ("train", "validation")  # never the sealed test period


def _signal(conn: sqlite3.Connection, market: str, pattern: str, conditions: list[dict], df: pd.DataFrame,
            end: str | None) -> pd.Series:
    if pattern not in patterns.PATTERNS:
        raise SpecError(f"unknown pattern {pattern!r}; known: {', '.join(patterns.PATTERNS)}")
    return trials.signal_function(conn, {"pattern": pattern, "conditions": conditions}, end, {market: df})(market, df)


def conditional_baseline(conn: sqlite3.Connection, market: str, horizon: int, conditions: list[dict],
                         period: str) -> dict:
    if period not in BASELINE_PERIODS:
        raise SpecError(f"period must be one of {', '.join(BASELINE_PERIODS)}; the test period is sealed")
    start, end = trials.PERIODS[period]
    df = load_daily(conn, market, end)
    mask = pd.Series(True, index=df.index)
    cache = {market: df}

    def load(m: str) -> pd.DataFrame:
        if m not in cache:
            cache[m] = load_daily(conn, m, end)
        return cache[m]

    for condition in conditions:
        mask &= patterns.condition_mask(condition, df.index, load, market)
    fwd, on = backtest.forward_series(df, mask, horizon, pd.Timestamp(start) if start else None)
    stats = lambda x: {"days": int(len(x)), "approx_independent": int(len(x)) // horizon,
                       "share_up": round(float((x > 0).mean()), 4) if len(x) else None,
                       "mean_return": round(float(x.mean()), 6) if len(x) else None}
    return {"market": market, "period": period, "horizon_days": horizon, "conditions": conditions,
            "matching_days": stats(fwd[on]), "all_days": stats(fwd),
            "note": "overlapping windows: days overstate the independent evidence; descriptive, not a trial"}


def add_monitor(conn: sqlite3.Connection, hypothesis_id: int, market: str, pattern: str, conditions: list[dict],
                horizon: int, created_by: str) -> dict:
    h = hypotheses.show(conn, hypothesis_id)
    if h["status"] == "retired":
        raise hypotheses.RegistryError(f"hypothesis {hypothesis_id} is retired")
    if horizon not in predictions.BASELINE_HORIZONS:
        raise hypotheses.RegistryError(f"horizon must be one of {predictions.BASELINE_HORIZONS}, so each "
                                       "prediction pairs with a base_rate_v1 prediction on the scorecard")
    df = load_daily(conn, market)
    _signal(conn, market, pattern, conditions, df, None)  # validates the pattern and conditions now
    with conn:
        mid = conn.execute("INSERT INTO hypothesis_monitors (hypothesis_id, market, pattern, conditions, horizon_days,"
                           " created_by) VALUES (?, ?, ?, ?, ?, ?)",
                           (hypothesis_id, market, pattern, json.dumps(conditions, sort_keys=True), horizon,
                            created_by)).lastrowid
    return {"monitor_id": mid, "model": f"hyp_{hypothesis_id}"}


def list_monitors(conn: sqlite3.Connection) -> list[dict]:
    rows = [dict(r) for r in conn.execute("SELECT * FROM hypothesis_monitors ORDER BY id")]
    for r in rows:
        r["conditions"] = json.loads(r["conditions"])
        r["hypothesis_status"] = hypotheses.status(conn, r["hypothesis_id"])
        r["predictions"] = [dict(p) for p in conn.execute(
            "SELECT p.id, p.as_of, p.prob_up, o.up, o.brier FROM predictions p JOIN markets m ON m.id = p.market_id"
            " JOIN sources s ON s.id = m.source_id LEFT JOIN outcomes o ON o.prediction_id = p.id"
            " WHERE p.model = ? AND s.name || ':' || m.symbol = ? AND p.horizon_days = ? ORDER BY p.id",
            (f"hyp_{r['hypothesis_id']}", r["market"], r["horizon_days"]))]
    return rows


def run(conn: sqlite3.Connection) -> list[dict]:
    """Check every live monitor on its market's latest candle; record a prediction where it fires."""
    out = []
    for m in list_monitors(conn):
        if m["hypothesis_status"] == "retired":
            continue
        df = load_daily(conn, m["market"])
        signal = _signal(conn, m["market"], m["pattern"], m["conditions"], df, None)
        as_of = df.index[-1].date().isoformat()
        row = {"monitor_id": m["id"], "hypothesis_id": m["hypothesis_id"], "market": m["market"], "as_of": as_of,
               "horizon_days": m["horizon_days"], "active": bool(signal.iloc[-1])}
        model = f"hyp_{m['hypothesis_id']}"
        if row["active"] and not any(p["as_of"] == as_of for p in m["predictions"]):
            fwd, on = backtest.forward_series(df, signal, m["horizon_days"], None)
            past = fwd[on]
            k, n, base = int((past > 0).sum()), int(len(past)), float((fwd > 0).mean())
            prob = (k + PRIOR_WEIGHT * base) / (n + PRIOR_WEIGHT)
            evidence = {"version": VERSION, "pattern": m["pattern"], "conditions": m["conditions"],
                        "past_signal_windows": n, "past_up": k, "base_share_up": round(base, 4),
                        "prior_weight": PRIOR_WEIGHT}
            pid = predictions.record(conn, m["market"], m["horizon_days"], round(prob, 4), "model", model,
                                     rationale=f"{VERSION}: hypothesis {m['hypothesis_id']} signal active",
                                     evidence=evidence)
            hypotheses.add_evidence(conn, m["hypothesis_id"], direction="neutral", basis="forward",
                                    summary=f"Signal {m['pattern']} active on {as_of}; prediction {pid} recorded "
                                            f"(P(up {m['horizon_days']}d) = {prob:.3f}); judge when it resolves",
                                    cite=f"prediction:{pid}", created_by="agent")
            row.update(prediction_id=pid, prob_up=round(prob, 4), past_signal_windows=n)
        out.append(row)
    return out
