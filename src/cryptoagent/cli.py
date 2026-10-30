"""Command-line entry point: `python -m cryptoagent <command>`.

Commands that read data print JSON so skills and future AI runners can consume them
directly. Validation problems exit with status 2.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

from cryptoagent import db, predictions, quality, report, trials
from cryptoagent.collectors import COLLECTORS
from cryptoagent.config import load_settings
from cryptoagent.predictions import LedgerError
from cryptoagent.quant import indicators, patterns, regime
from cryptoagent.quant.backtest import Costs
from cryptoagent.quant.patterns import SpecError
from cryptoagent.quant.series import UnknownMarket, load, load_daily


class UsageError(Exception):
    pass


def _out(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def _jsonable(row) -> dict:
    return {k: (None if isinstance(v, float) and v != v else v) for k, v in row.items()}


def parse_when(text: str) -> dict:
    """'[MARKET] FIELD OP VALUE', e.g. 'bitstamp:BTC/USD sma50_change20 > 0' or 'trend == uptrend'."""
    parts = text.split()
    if len(parts) not in (3, 4) or parts[-2] not in patterns.OPS:
        raise UsageError(f"--when expects '[MARKET] FIELD OP VALUE' with OP in {' '.join(patterns.OPS)}; got {text!r}")
    *market, field, op, raw = parts
    try:
        value = float(raw)
    except ValueError:
        value = raw
    return {**({"market": market[0]} if market else {}), "field": field, "op": op, "value": value}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cryptoagent")
    parser.add_argument("--db", help="database path (default: CRYPTOAGENT_DB_PATH or data/cryptoagent.db)")
    cmd = parser.add_subparsers(dest="command", required=True)

    cmd.add_parser("init-db", help="create the database and apply pending migrations")
    cmd.add_parser("db-status", help="applied/pending migrations and tables")
    cmd.add_parser("sources", help="list data collectors and whether each is configured")

    p = cmd.add_parser("collect", help="run a collector: backfills history, then extends to the last complete day")
    p.add_argument("name", choices=list(COLLECTORS))
    p.add_argument("--markets", help="comma-separated symbols to collect candles for, e.g. BTC/USD,ETH/USD")

    p = cmd.add_parser("quality", help="run data-quality checks and summarise recorded issues")
    p.add_argument("--no-run", action="store_true", help="only summarise issues already recorded")
    p.add_argument("--market", help="limit the summary to one market, e.g. coinbase:BTC/USD")
    p.add_argument("--limit", type=int, default=20, help="number of recent issues to list")
    p.add_argument("--outlier-return", type=float, default=quality.OUTLIER_RETURN)
    p.add_argument("--reference-tolerance", type=float, default=quality.REFERENCE_TOLERANCE)

    p = cmd.add_parser("markets", help="markets with latest listing status and stored candle range")
    p.add_argument("--source")
    p.add_argument("--status", help="latest exchange status, e.g. online, delisted")

    p = cmd.add_parser("candles", help="stored daily candles for one market")
    p.add_argument("market", help="SOURCE:SYMBOL, e.g. coinbase:BTC/USD")
    p.add_argument("--last", type=int, default=30, help="number of most recent candles")

    p = cmd.add_parser("indicators", help="indicator values for one market (latest bar, or as of a past day)")
    p.add_argument("market", help="SOURCE:SYMBOL, e.g. coinbase:BTC/USD")
    p.add_argument("--as-of", help="YYYY-MM-DD: use only candles up to this day")
    p.add_argument("--last", type=int, default=1, help="number of most recent bars")
    p.add_argument("--timeframe", choices=["1d", "1w"], default="1d")

    p = cmd.add_parser("patterns", help="which defined setups are active on the latest bar, with their history")
    p.add_argument("market", help="SOURCE:SYMBOL")
    p.add_argument("--as-of", help="YYYY-MM-DD: use only candles up to this day")
    p.add_argument("--timeframe", choices=["1d", "1w"], default="1d")

    p = cmd.add_parser("backtest", help="backtest one setup on a chronological period; always recorded as a trial")
    p.add_argument("--market", required=True)
    p.add_argument("--pattern", required=True, choices=list(patterns.PATTERNS))
    p.add_argument("--when", action="append", default=[], metavar="'[MARKET] FIELD OP VALUE'",
                   help="filter condition on the signal day; repeatable (all must hold)")
    p.add_argument("--hold", type=int, required=True, help="time exit: close of signal day + N")
    p.add_argument("--stop-atr", type=float, help="stop at entry - K x ATR14")
    p.add_argument("--target-atr", type=float, help="target at entry + M x ATR14")
    p.add_argument("--period", required=True, choices=list(trials.PERIODS))
    p.add_argument("--family", required=True, help="short name grouping variants of one idea")
    p.add_argument("--hypothesis", required=True, help="what this run tests, stated before seeing results")
    p.add_argument("--by", required=True, help="who ran it, e.g. claude or user")
    p.add_argument("--fee", type=float, default=Costs.fee, help="per side (placeholder until confirmed)")
    p.add_argument("--slippage", type=float, default=Costs.slippage, help="per side")
    p.add_argument("--trades", action="store_true", help="include the trade list")

    tr = cmd.add_parser("trials", help="the trial registry").add_subparsers(dest="action", required=True)
    p = tr.add_parser("list")
    p.add_argument("--family")
    p.add_argument("--limit", type=int, default=50)
    tr.add_parser("families", help="trial counts per hypothesis family")
    p = tr.add_parser("show", help="everything recorded for one trial (results, forward returns, cost sensitivity)")
    p.add_argument("id", type=int)

    p = cmd.add_parser("regime", help=f"{regime.VERSION} labels (descriptive, not trade signals)")
    p.add_argument("--market", default=report.REGIME_MARKET)
    p.add_argument("--as-of", help="YYYY-MM-DD: use only candles up to this day")

    predict = cmd.add_parser("predict", help="record predictions").add_subparsers(dest="action", required=True)
    p = predict.add_parser("add", help="record a judgment: P(close at as-of + horizon > close at as-of)")
    p.add_argument("--market", required=True)
    p.add_argument("--horizon", type=int, required=True, help="days")
    p.add_argument("--prob-up", type=float, required=True)
    p.add_argument("--by", dest="model", required=True, help="who made the judgment, e.g. claude")
    p.add_argument("--rationale", required=True)
    p.add_argument("--evidence", help="JSON object: the CLI outputs and figures the judgment rests on")
    p = predict.add_parser("baselines", help="record today's deterministic base-rate predictions")
    p.add_argument("--market", default=report.TRADE_MARKET)

    preds = cmd.add_parser("predictions", help="the prediction ledger").add_subparsers(dest="action", required=True)
    p = preds.add_parser("list")
    p.add_argument("--status", choices=["open", "resolved", "unresolvable"])
    p.add_argument("--source", choices=["model", "judgment"])
    p.add_argument("--limit", type=int, default=50)
    p = preds.add_parser("score", help="Brier, skill vs base rate, calibration, sample sizes")
    p.add_argument("--market")

    cmd.add_parser("evaluate", help="resolve predictions whose target day's candle is stored")

    p = cmd.add_parser("report", help="write the deterministic daily report to reports/YYYY-MM-DD.md")
    p.add_argument("--dir", default="reports")
    return parser


def _market_id(conn, spec: str) -> int:
    source, _, symbol = spec.partition(":")
    row = conn.execute(
        "SELECT m.id FROM markets m JOIN sources s ON s.id = m.source_id WHERE s.name = ? AND m.symbol = ?",
        (source, symbol),
    ).fetchone()
    if row is None:
        raise UsageError(f"unknown market {spec!r}; expected SOURCE:SYMBOL, see `markets`")
    return row[0]


def list_markets(conn, source: str | None, status: str | None) -> list[dict]:
    rows = conn.execute(
        """
        SELECT s.name AS source, m.symbol, m.venue_id, h.status, h.active, h.observed_at AS status_since,
               m.history_start_found_at, c.first_ts, c.last_ts, coalesce(c.n, 0) AS candles
        FROM markets m
        JOIN sources s ON s.id = m.source_id
        LEFT JOIN market_status_history h
               ON h.id = (SELECT max(id) FROM market_status_history WHERE market_id = m.id)
        LEFT JOIN (SELECT market_id, min(ts) AS first_ts, max(ts) AS last_ts, count(*) AS n
                   FROM candles WHERE timeframe = '1d' GROUP BY market_id) c ON c.market_id = m.id
        WHERE (? IS NULL OR s.name = ?) AND (? IS NULL OR h.status = ?)
        ORDER BY s.name, m.symbol
        """,
        (source, source, status, status),
    ).fetchall()
    return [dict(r) for r in rows]


def run(args, conn: sqlite3.Connection, settings) -> None:
    c = args.command
    if c == "init-db":
        _out({"applied": db.migrate(conn), "db": str(settings.db_path)})
    elif c == "db-status":
        _out(db.status(conn))
    elif c == "sources":
        _out([{"name": k, "kind": v.source_kind, "description": v.description, "configured": v.configured(settings)}
              for k, v in COLLECTORS.items()])
    elif c == "collect":
        markets = [s.strip() for s in args.markets.split(",") if s.strip()] if args.markets else None
        _out(COLLECTORS[args.name].collect(conn, settings, markets=markets))
    elif c == "quality":
        new = None if args.no_run else quality.run_checks(conn, args.outlier_return, args.reference_tolerance)
        if args.market:
            _market_id(conn, args.market)
        _out({"new_issues": new, **quality.summary(conn, args.market, args.limit)})
    elif c == "markets":
        _out(list_markets(conn, args.source, args.status))
    elif c == "candles":
        rows = conn.execute(
            "SELECT ts, open, high, low, close, volume FROM candles WHERE market_id = ? AND timeframe = '1d'"
            " ORDER BY ts DESC LIMIT ?",
            (_market_id(conn, args.market), args.last),
        ).fetchall()
        _out([dict(r) for r in reversed(rows)])
    elif c == "indicators":
        frame = indicators.compute_all(load(conn, args.market, args.timeframe, args.as_of)).tail(args.last)
        key = "day" if args.timeframe == "1d" else "week_of"
        _out([{key: day.date().isoformat(), **_jsonable(row)} for day, row in frame.iterrows()])
    elif c == "patterns":
        df = load(conn, args.market, args.timeframe, args.as_of)
        _out({"market": args.market, "timeframe": args.timeframe,
              "as_of": df.index[-1].date().isoformat() if len(df) else None, "patterns": patterns.active(df)})
    elif c == "backtest":
        spec = {"market": args.market, "pattern": args.pattern, "conditions": [parse_when(w) for w in args.when],
                "exit": {"hold_days": args.hold, "stop_atr": args.stop_atr, "target_atr": args.target_atr}}
        _out(trials.run_trial(conn, spec, args.period, args.family, args.hypothesis, args.by,
                              Costs(args.fee, args.slippage), include_trades=args.trades))
    elif c == "trials" and args.action == "list":
        _out(trials.list_trials(conn, args.family, args.limit))
    elif c == "trials" and args.action == "families":
        _out(trials.families(conn))
    elif c == "trials" and args.action == "show":
        _out(trials.show(conn, args.id))
    elif c == "regime":
        _out(regime.snapshot(load_daily(conn, args.market, args.as_of)))
    elif c == "predict" and args.action == "add":
        try:
            evidence = json.loads(args.evidence) if args.evidence else None
        except json.JSONDecodeError as exc:
            raise UsageError(f"--evidence is not valid JSON: {exc}") from exc
        pid = predictions.record(conn, args.market, args.horizon, args.prob_up, "judgment", args.model,
                                 args.rationale, evidence)
        _out(predictions.list_predictions(conn, limit=1)[0] | {"id": pid})
    elif c == "predict" and args.action == "baselines":
        _out(predictions.record_baselines(conn, args.market))
    elif c == "predictions" and args.action == "list":
        _out(predictions.list_predictions(conn, args.status, args.source, args.limit))
    elif c == "predictions" and args.action == "score":
        _out(predictions.scorecard(conn, args.market))
    elif c == "evaluate":
        _out(predictions.evaluate(conn))
    elif c == "report":
        print(report.write(conn, Path(args.dir)))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    if args.db:
        settings = replace(settings, db_path=Path(args.db))
    conn = db.connect(settings.db_path)
    try:
        if args.command not in ("init-db", "db-status") and db.status(conn)["pending"]:
            print("database has pending migrations; run `python -m cryptoagent init-db` first", file=sys.stderr)
            return 2
        run(args, conn, settings)
    except (UsageError, UnknownMarket, LedgerError, SpecError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except sqlite3.IntegrityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
