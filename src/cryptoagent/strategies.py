"""Strategy lifecycle under promotion-v1: propose -> check -> (user) authorize-test -> test -> paper
-> review -> (user) approve / reject; approved -> (user) retire.

A strategy is a backtest spec that has to earn the right to produce signals. Each stage is an
append-only event; the database only accepts allowed transitions and only accepts the user for
authorising the sealed test, approving, rejecting or retiring. The gates below are the
definition of promotion-v1: changing one makes a new version that the user approves.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import numpy as np
import pandas as pd

from cryptoagent import trials
from cryptoagent.quant import backtest
from cryptoagent.quant import indicators as ind
from cryptoagent.quant import universe
from cryptoagent.quant.backtest import Costs, Exit
from cryptoagent.quant.patterns import SpecError
from cryptoagent.quant.series import load_daily, market_id

POLICY = "promotion-v1"
VALIDATION_GATE = {"min_trades": 30, "share_positive_years_min": 0.5}  # plus CI90 low > 0, double costs > 0, beats drift
TEST_GATE = {"min_trades": 10}  # plus mean > 0 and double-cost mean > 0
PAPER_GATE = {"min_days": 90, "min_resolved": 10}  # plus mean paper return > 0 after costs
TRACKED_STAGES = ("paper", "paper_review", "approved")  # stages whose signals are recorded as paper trades


class StrategyError(ValueError):
    pass


def _latest_version(conn, name: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT v.* FROM strategy_versions v JOIN strategies s ON s.id = v.strategy_id WHERE s.name = ?"
        " ORDER BY v.version DESC LIMIT 1", (name,)).fetchone()
    if row is None:
        raise StrategyError(f"unknown strategy {name!r}; see `strategy list`")
    return row


def stage(conn, version_id: int) -> str | None:
    row = conn.execute("SELECT event FROM strategy_events WHERE strategy_version_id = ? ORDER BY id DESC LIMIT 1",
                       (version_id,)).fetchone()
    return row[0] if row else None


def _event(conn, version_id: int, event: str, decided_by: str, note: str | None = None,
           evidence: dict | None = None) -> None:
    with conn:
        conn.execute("INSERT INTO strategy_events (strategy_version_id, event, decided_by, note, evidence)"
                     " VALUES (?, ?, ?, ?, ?)", (version_id, event, decided_by, note,
                                                  json.dumps(evidence, sort_keys=True, default=str) if evidence else None))


def propose(conn: sqlite3.Connection, name: str, spec: dict, rationale: str, created_by: str) -> dict:
    norm, spec_hash = trials.canonical(spec)
    with conn:
        conn.execute("INSERT OR IGNORE INTO strategies (name) VALUES (?)", (name,))
        strategy_id = conn.execute("SELECT id FROM strategies WHERE name = ?", (name,)).fetchone()[0]
        version = conn.execute("SELECT coalesce(max(version), 0) + 1 FROM strategy_versions WHERE strategy_id = ?",
                               (strategy_id,)).fetchone()[0]
        version_id = conn.execute(
            "INSERT INTO strategy_versions (strategy_id, version, spec, spec_hash, rationale, created_by)"
            " VALUES (?, ?, ?, ?, ?, ?)", (strategy_id, version, json.dumps(norm, sort_keys=True), spec_hash,
                                           rationale, created_by)).lastrowid
    _event(conn, version_id, "proposed", "agent" if created_by == "agent" else "user")
    return {"strategy": name, "version": version, "spec_hash": spec_hash, "stage": "proposed"}


def _trial(conn, spec_hash: str, period: str) -> dict | None:
    row = conn.execute("SELECT id FROM trials WHERE spec_hash = ? AND period = ? ORDER BY id DESC LIMIT 1",
                       (spec_hash, period)).fetchone()
    return trials.show(conn, row[0]) if row else None


def validation_gate(results: dict) -> dict:
    fwd = results.get("forward_returns", {})
    after = (fwd.get("after_signal") or {}).get("mean")
    every = (fwd.get("all_days") or fwd.get("all_member_days") or {}).get("mean")
    ci = results.get("mean_return_ci90") or [None, None]
    share = (results.get("windows") or {}).get("summary", {}).get("share_positive_mean")
    double = (results.get("cost_sensitivity") or {}).get("double_costs", {}).get("mean_return")
    checks = {
        f"trades >= {VALIDATION_GATE['min_trades']}": results.get("trades", 0) >= VALIDATION_GATE["min_trades"],
        "CI90 lower bound > 0": ci[0] is not None and ci[0] > 0,
        "mean > 0 at double costs": double is not None and double > 0,
        "forward return after signal > all days": after is not None and every is not None and after > every,
        f"positive mean in >= {VALIDATION_GATE['share_positive_years_min']:.0%} of years with trades":
            share is not None and share >= VALIDATION_GATE["share_positive_years_min"],
    }
    return {"passed": all(checks.values()), "checks": checks}


def check(conn: sqlite3.Connection, name: str) -> dict:
    v = _latest_version(conn, name)
    if stage(conn, v["id"]) != "proposed":
        raise StrategyError(f"{name} v{v['version']} is at stage {stage(conn, v['id'])!r}; check runs on 'proposed'")
    train, validation = _trial(conn, v["spec_hash"], "train"), _trial(conn, v["spec_hash"], "validation")
    if validation is None:
        raise StrategyError("no validation trial of this exact spec yet: run `backtest ... --period validation` first")
    gate = validation_gate(validation["results"])
    train_ok = train is not None and (train["results"].get("mean_return") or 0) > 0
    gate["checks"]["train trial exists with mean > 0"] = train_ok
    gate["passed"] = gate["passed"] and train_ok
    counts = conn.execute("SELECT count(*), sum(family = ?) FROM trials", (validation["family"],)).fetchone()
    evidence = {"policy": POLICY, "train_trial": train and train["id"], "validation_trial": validation["id"],
                "checks": gate["checks"], "trials_total": counts[0], "trials_in_family": counts[1]}
    event = "validation_passed" if gate["passed"] else "validation_failed"
    _event(conn, v["id"], event, "system", evidence=evidence)
    return {"strategy": name, "version": v["version"], "stage": event, **evidence}


def authorize_test(conn: sqlite3.Connection, name: str, decided_by: str, note: str | None = None) -> dict:
    v = _latest_version(conn, name)
    _event(conn, v["id"], "test_authorized", decided_by, note)
    return {"strategy": name, "version": v["version"], "stage": "test_authorized"}


def run_test(conn: sqlite3.Connection, name: str, created_by: str) -> dict:
    v = _latest_version(conn, name)
    if stage(conn, v["id"]) != "test_authorized":
        raise StrategyError("the sealed test needs the user's authorisation first (`strategy authorize-test`)")
    spec = json.loads(v["spec"])
    out = trials.run_trial(conn, spec, "test", f"strategy:{name}", f"{POLICY} sealed test of {name} v{v['version']}",
                           created_by)
    double = out["cost_sensitivity"]["double_costs"]["mean_return"]
    checks = {f"trades >= {TEST_GATE['min_trades']}": out["trades"] >= TEST_GATE["min_trades"],
              "mean > 0": (out.get("mean_return") or 0) > 0, "mean > 0 at double costs": (double or 0) > 0}
    passed = all(checks.values())
    evidence = {"policy": POLICY, "test_trial": out["trial_id"], "checks": checks}
    _event(conn, v["id"], "test_passed" if passed else "test_failed", "system", evidence=evidence)
    if passed:
        _event(conn, v["id"], "paper", "system", note="paper forward test started")
    return {"strategy": name, "version": v["version"], "stage": "paper" if passed else "test_failed", **evidence}


def decide(conn: sqlite3.Connection, name: str, event: str, decided_by: str, note: str | None = None) -> dict:
    if event not in ("approved", "rejected", "retired"):
        raise StrategyError("decision must be approved, rejected or retired")
    v = _latest_version(conn, name)
    _event(conn, v["id"], event, decided_by, note)
    return {"strategy": name, "version": v["version"], "stage": event}


def _markets_today(conn, norm: dict) -> dict[str, pd.DataFrame]:
    if not norm["market"].startswith(trials.POOL_PREFIX):
        return {norm["market"]: load_daily(conn, norm["market"])}
    version = norm["market"][len(trials.POOL_PREFIX):]
    frames = universe._candles(conn, "coinbase", "USD", "9999-12-31")
    if not frames:
        return {}
    latest = max(df.index[-1] for df in frames.values())
    return {f"coinbase:{s}": df for s, df in frames.items()
            if universe.membership(df, latest, s.split("/")[0], version)[0]}


def paper_step(conn: sqlite3.Connection) -> dict:
    """Record today's paper signals for tracked strategies, resolve finished paper trades, apply the review gate."""
    summary = {"signals": 0, "resolved": 0, "reviews": []}
    versions = [v for v in conn.execute("SELECT v.*, s.name FROM strategy_versions v JOIN strategies s"
                                        " ON s.id = v.strategy_id").fetchall() if stage(conn, v["id"]) in TRACKED_STAGES]
    for v in versions:
        norm = json.loads(v["spec"])
        frames = _markets_today(conn, norm)
        signal_for = trials.signal_function(conn, norm, None, frames)
        for market, df in frames.items():
            if len(df) < 2 or not bool(signal_for(market, df).iloc[-1]):
                continue
            mid = market_id(conn, market)
            if conn.execute("SELECT 1 FROM paper_trades t LEFT JOIN paper_outcomes o ON o.paper_trade_id = t.id"
                            " WHERE t.strategy_version_id = ? AND t.market_id = ? AND o.paper_trade_id IS NULL",
                            (v["id"], mid)).fetchone():
                continue  # one open paper trade per market
            atr = float(ind.atr(df, 14).iloc[-1])
            with conn:
                summary["signals"] += conn.execute(
                    "INSERT OR IGNORE INTO paper_trades (strategy_version_id, market_id, as_of, as_of_close, atr)"
                    " VALUES (?, ?, ?, ?, ?)", (v["id"], mid, df.index[-1].date().isoformat(),
                                                float(df["close"].iloc[-1]), atr if atr == atr else None)).rowcount
        summary["resolved"] += _resolve(conn, v, norm)
        if stage(conn, v["id"]) == "paper":
            gate = paper_gate(conn, v["id"])
            if gate["passed"]:
                _event(conn, v["id"], "paper_review", "system", evidence=gate)
                summary["reviews"].append(v["name"])
    return summary


def _resolve(conn, v, norm: dict) -> int:
    ex = norm["exit"]
    exit = Exit(int(ex["hold_days"]), ex.get("stop_atr"), ex.get("target_atr"))
    resolved = 0
    for t in conn.execute("SELECT t.*, s.name || ':' || m.symbol AS market FROM paper_trades t"
                          " JOIN markets m ON m.id = t.market_id JOIN sources s ON s.id = m.source_id"
                          " LEFT JOIN paper_outcomes o ON o.paper_trade_id = t.id"
                          " WHERE t.strategy_version_id = ? AND o.paper_trade_id IS NULL", (v["id"],)).fetchall():
        end = (date.fromisoformat(t["as_of"]) + timedelta(days=exit.hold_days)).isoformat()
        df = load_daily(conn, t["market"], end)
        window = df[df.index >= pd.Timestamp(t["as_of"])]
        if window.index[-1].date().isoformat() < end:
            continue  # horizon not over yet (or the market stopped trading: resolved once data runs past it)
        signal = pd.Series(False, index=window.index)
        signal.iloc[0] = True
        atr = pd.Series(t["atr"] or np.nan, index=window.index)
        trades, _ = backtest.simulate(window, signal, exit, Costs(), atr=atr)
        with conn:
            if trades:
                x = trades[0]
                conn.execute("INSERT INTO paper_outcomes (paper_trade_id, status, trade_return, exit_reason, entry_price,"
                             " exit_price, exit_day) VALUES (?, 'resolved', ?, ?, ?, ?, ?)",
                             (t["id"], x["return"], x["reason"], x["entry"], x["exit"], x["exit_day"]))
            else:
                conn.execute("INSERT INTO paper_outcomes (paper_trade_id, status) VALUES (?, 'unresolvable')", (t["id"],))
        resolved += 1
    return resolved


def paper_gate(conn: sqlite3.Connection, version_id: int) -> dict:
    started = conn.execute("SELECT substr(min(created_at), 1, 10) FROM strategy_events WHERE strategy_version_id = ?"
                           " AND event = 'paper'", (version_id,)).fetchone()[0]
    returns = [r[0] for r in conn.execute(
        "SELECT o.trade_return FROM paper_outcomes o JOIN paper_trades t ON t.id = o.paper_trade_id"
        " WHERE t.strategy_version_id = ? AND o.status = 'resolved'", (version_id,))]
    days = (date.today() - date.fromisoformat(started)).days if started else 0
    mean = float(np.mean(returns)) if returns else None
    checks = {f"paper days >= {PAPER_GATE['min_days']}": days >= PAPER_GATE["min_days"],
              f"resolved paper trades >= {PAPER_GATE['min_resolved']}": len(returns) >= PAPER_GATE["min_resolved"],
              "mean paper return > 0 after costs": mean is not None and mean > 0}
    return {"policy": POLICY, "passed": all(checks.values()), "checks": checks, "paper_days": days,
            "resolved_trades": len(returns), "mean_return": round(mean, 6) if mean is not None else None}


def list_strategies(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for v in conn.execute("SELECT v.*, s.name FROM strategy_versions v JOIN strategies s ON s.id = v.strategy_id"
                          " ORDER BY s.name, v.version").fetchall():
        events = [dict(e) for e in conn.execute(
            "SELECT event, decided_by, created_at, note FROM strategy_events WHERE strategy_version_id = ? ORDER BY id",
            (v["id"],))]
        row = {"strategy": v["name"], "version": v["version"], "stage": events[-1]["event"] if events else None,
               "spec": json.loads(v["spec"]), "rationale": v["rationale"], "created_by": v["created_by"],
               "history": events}
        if row["stage"] in TRACKED_STAGES:
            row["paper"] = paper_gate(conn, v["id"])
        out.append(row)
    return out


def approved_signals(conn: sqlite3.Connection, as_of: str | None = None) -> list[dict]:
    """Paper trades opened on the latest day by approved strategies: the signals the user may act on."""
    rows = conn.execute(
        "SELECT s.name AS strategy, v.version, so.name || ':' || m.symbol AS market, t.as_of, t.as_of_close, t.atr,"
        " v.spec FROM paper_trades t JOIN strategy_versions v ON v.id = t.strategy_version_id"
        " JOIN strategies s ON s.id = v.strategy_id JOIN markets m ON m.id = t.market_id"
        " JOIN sources so ON so.id = m.source_id WHERE t.as_of = coalesce(?, (SELECT max(as_of) FROM paper_trades))",
        (as_of,)).fetchall()
    out = []
    for r in rows:
        version_id = conn.execute("SELECT v.id FROM strategy_versions v JOIN strategies s ON s.id = v.strategy_id"
                                  " WHERE s.name = ? AND v.version = ?", (r["strategy"], r["version"])).fetchone()[0]
        if stage(conn, version_id) == "approved":
            spec = json.loads(r["spec"])
            out.append({k: r[k] for k in ("strategy", "version", "market", "as_of", "as_of_close", "atr")}
                       | {"exit": spec["exit"]})
    return out
