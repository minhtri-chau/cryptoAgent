"""Run a backtest specification on a chronological period and record it as a trial.

Periods are the same dates for every market (docs/architecture.md, v0.3), so conditions
on BTC can't carry information across them. The test period is sealed: the database
allows one test run per specification, and only after a validation run.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pandas as pd

from cryptoagent.quant import analogs, backtest, patterns, pooled, universe
from cryptoagent.quant.backtest import Costs, Exit
from cryptoagent.quant.patterns import SpecError
from cryptoagent.quant.series import load_daily

PERIODS = {
    "train": (None, "2021-12-31"),
    "validation": ("2022-01-01", "2024-06-30"),
    "test": ("2024-07-01", None),
}
SPEC_KEYS = {"market", "pattern", "conditions", "exit"}
POOL_PREFIX = "universe:"  # market "universe:universe-v2" = every member market, point-in-time
EXIT_KEYS = {"hold_days", "stop_atr", "target_atr"}


def canonical(spec: dict) -> tuple[dict, str]:
    """Validate a specification and return it normalised, with its hash."""
    if not isinstance(spec, dict) or set(spec) - SPEC_KEYS or not {"market", "pattern", "exit"} <= set(spec):
        raise SpecError(f"spec needs market, pattern, exit (and optional conditions); got {spec}")
    if spec["pattern"] not in patterns.PATTERNS:
        raise SpecError(f"unknown pattern {spec['pattern']!r}; known: {', '.join(patterns.PATTERNS)}")
    if spec["market"].startswith(POOL_PREFIX) and spec["market"][len(POOL_PREFIX):] not in universe.VERSIONS:
        raise SpecError(f"unknown universe in {spec['market']!r}; use {POOL_PREFIX}" + f" or {POOL_PREFIX}".join(
            universe.VERSIONS))
    ex = spec["exit"]
    if not isinstance(ex, dict) or set(ex) - EXIT_KEYS or "hold_days" not in ex:
        raise SpecError(f"exit needs hold_days (and optional stop_atr, target_atr); got {ex}")
    try:
        Exit(**ex)
    except (TypeError, ValueError) as exc:
        raise SpecError(f"invalid exit: {exc}") from exc
    conditions = spec.get("conditions") or []
    if not isinstance(conditions, list):
        raise SpecError("conditions must be a list")
    number = lambda v: None if v is None else float(v)  # 2 and 2.0 must hash the same
    norm = {"market": spec["market"], "pattern": spec["pattern"],
            "conditions": [dict(sorted(c.items())) for c in conditions],
            "exit": {"hold_days": int(ex["hold_days"]), "stop_atr": number(ex.get("stop_atr")),
                     "target_atr": number(ex.get("target_atr"))}}
    text = json.dumps(norm, sort_keys=True, separators=(",", ":"))
    return norm, hashlib.sha256(text.encode()).hexdigest()[:16]


def run_trial(conn: sqlite3.Connection, spec: dict, period: str, family: str, hypothesis: str, created_by: str,
              costs: Costs = Costs(), include_trades: bool = False) -> dict:
    if period not in PERIODS:
        raise SpecError(f"unknown period {period!r}; use {', '.join(PERIODS)}")
    norm, spec_hash = canonical(spec)
    start, end = PERIODS[period]
    exit = Exit(**{k: v for k, v in norm["exit"].items() if v is not None})
    if norm["market"].startswith(POOL_PREFIX):
        result, first_day, last_day = _run_pooled(conn, norm, start, end, exit, costs)
    else:
        df = load_daily(conn, norm["market"], end)  # nothing after the period is ever loaded
        if start is not None and (df.empty or df.index[-1] < pd.Timestamp(start)):
            raise SpecError(f"{norm['market']} has no data in the {period} period")
        signal = signal_function(conn, norm, end, {norm["market"]: df})(norm["market"], df)
        result = backtest.run(df, signal, exit, costs, pd.Timestamp(start) if start else None)
        first_day, last_day = df.index[0], df.index[-1]
    trade_list = result.pop("trade_list")
    data_through = last_day.date().isoformat()
    actual_start = max(first_day, pd.Timestamp(start)) if start else first_day
    with conn:
        trial_id = conn.execute(
            "INSERT INTO trials (created_by, family, hypothesis, spec, spec_hash, market, period, period_start,"
            " period_end, data_through, costs, trades, mean_return, results) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?)",
            (created_by, family, hypothesis, json.dumps(norm, sort_keys=True), spec_hash, norm["market"], period,
             actual_start.date().isoformat(), data_through, data_through, json.dumps(result["costs"]),
             result["trades"], result.get("mean_return"), json.dumps(result, sort_keys=True, default=str)),
        ).lastrowid
    counts = conn.execute(
        "SELECT count(*), sum(family = ?), sum(spec_hash = ?) FROM trials", (family, spec_hash)).fetchone()
    out = {"trial_id": trial_id, "spec_hash": spec_hash, "period": period,
           "period_range": [actual_start.date().isoformat(), data_through], "spec": norm, **result,
           "trials_total": counts[0], "trials_in_family": counts[1], "trials_of_this_spec": counts[2]}
    if not result["costs"]["confirmed"]:
        out["warning_costs"] = "fees are a placeholder until the user confirms their Coinbase fee tier"
    if result["trades"] < 30:
        out["warning_sample"] = f"only {result['trades']} trades: too few to conclude anything"
    if include_trades:
        out["trade_list"] = trade_list
    return out


def signal_function(conn: sqlite3.Connection, norm: dict, end: str | None, frames: dict | None = None):
    """The spec's signal for any market's frame: its pattern AND every condition, on the same day.

    Shared by backtests, pooled runs and paper trading so a signal means the same thing everywhere.
    Other markets referenced by conditions are loaded up to `end` and computed once.
    """
    cache = dict(frames or {})
    fields_cache: dict[str, pd.DataFrame] = {}

    def load(market: str) -> pd.DataFrame:
        if market not in cache:
            cache[market] = load_daily(conn, market, end)
        return cache[market]

    def signal_for(market: str, df: pd.DataFrame) -> pd.Series:
        signal = patterns.evaluate(norm["pattern"], df)
        for condition in norm["conditions"]:
            shared = "market" in condition and condition["market"] != market
            signal &= patterns.condition_mask(condition, df.index, load, market, fields_cache if shared else None)
        return signal

    return signal_for


def _run_pooled(conn: sqlite3.Connection, norm: dict, start: str | None, end: str | None, exit: Exit,
                costs: Costs) -> tuple[dict, pd.Timestamp, pd.Timestamp]:
    """Every Coinbase USD market (delisted ones included), signals only on universe-member days."""
    version = norm["market"][len(POOL_PREFIX):]
    raw = universe._candles(conn, "coinbase", "USD", end or "9999-12-31")
    frames = {f"coinbase:{symbol}": df for symbol, df in raw.items()}
    if not frames:
        raise SpecError("no Coinbase USD candles stored")
    signal_for = signal_function(conn, norm, end, frames)

    def member_for(market: str, df: pd.DataFrame) -> pd.Series:
        return universe.member_mask(df, market.split(":", 1)[1].split("/")[0], version)

    result = pooled.run(frames, signal_for, member_for, exit, costs, pd.Timestamp(start) if start else None)
    first = min(df.index[0] for df in frames.values())
    last = max(df.index[-1] for df in frames.values())
    if start is not None and last < pd.Timestamp(start):
        raise SpecError(f"no data in the period starting {start}")
    return result, first, last


def run_analog_trial(conn: sqlite3.Connection, market: str, horizon: int, k: int, period: str, family: str,
                     hypothesis: str, created_by: str) -> dict:
    """Walk-forward evaluation of analog-v1 against the base rate on one period, recorded as a trial.

    The spec is {"method": "analog-v1", market, horizon_days, k}; the test period is sealed for it
    exactly as for backtests. Evaluation dates are `horizon` days apart and every outcome falls
    inside the period, because nothing after the period end is loaded.
    """
    if period not in PERIODS:
        raise SpecError(f"unknown period {period!r}; use {', '.join(PERIODS)}")
    if horizon < 1 or k < 1:
        raise SpecError("horizon and k must be positive")
    norm = {"method": analogs.VERSION, "market": market, "horizon_days": int(horizon), "k": int(k)}
    spec_hash = hashlib.sha256(json.dumps(norm, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    start, end = PERIODS[period]
    df = load_daily(conn, market, end)
    if df.empty or (start is not None and df.index[-1] < pd.Timestamp(start)):
        raise SpecError(f"{market} has no data in the {period} period")
    result = analogs.evaluate(df, horizon, k, pd.Timestamp(start) if start else None)
    dates = result.pop("dates", [])
    data_through = df.index[-1].date().isoformat()
    with conn:
        trial_id = conn.execute(
            "INSERT INTO trials (created_by, family, hypothesis, spec, spec_hash, market, period, period_start,"
            " period_end, data_through, costs, trades, mean_return, results) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " '{}', ?, NULL, ?)",
            (created_by, family, hypothesis, json.dumps(norm, sort_keys=True), spec_hash, market, period,
             result.get("first_day"), data_through, data_through, result["evaluations"],
             json.dumps(result | {"dates": dates}, sort_keys=True, default=str)),
        ).lastrowid
    counts = conn.execute("SELECT count(*), sum(family = ?) FROM trials", (family,)).fetchone()
    out = {"trial_id": trial_id, "spec_hash": spec_hash, "period": period, "spec": norm, **result,
           "trials_total": counts[0], "trials_in_family": counts[1]}
    if result["evaluations"] < 30:
        out["warning_sample"] = f"only {result['evaluations']} non-overlapping evaluations: too few to conclude"
    return out


def list_trials(conn: sqlite3.Connection, family: str | None = None, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT id, created_at, created_by, family, hypothesis, spec_hash, market, period, period_start, period_end,"
        " trades, mean_return, json_extract(results, '$.win_rate') AS win_rate,"
        " json_extract(results, '$.mean_return_ci90') AS mean_return_ci90,"
        " json_extract(results, '$.total_return') AS total_return, spec FROM trials"
        " WHERE (? IS NULL OR family = ?) ORDER BY id DESC LIMIT ?",
        (family, family, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["spec"] = json.loads(d["spec"])
        d["mean_return_ci90"] = json.loads(d["mean_return_ci90"]) if d["mean_return_ci90"] else None
        out.append(d)
    return out


def show(conn: sqlite3.Connection, trial_id: int) -> dict:
    """One trial with everything recorded for it, so any reported number can be traced."""
    row = conn.execute("SELECT * FROM trials WHERE id = ?", (trial_id,)).fetchone()
    if row is None:
        raise SpecError(f"no trial {trial_id}")
    out = dict(row)
    for key in ("spec", "costs", "results"):
        out[key] = json.loads(out[key])
    return out


def families(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT family, count(*) AS trials, count(DISTINCT spec_hash) AS specs, sum(period = 'test') AS test_runs,"
        " min(created_at) AS first, max(created_at) AS last FROM trials GROUP BY family ORDER BY last DESC"
    ).fetchall()
    return [dict(r) for r in rows]
