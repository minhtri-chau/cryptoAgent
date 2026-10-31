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

from cryptoagent import (backup, calls, coingecko, db, digest, dossier, fundamentals, predictions, privacy, quality,
                         recommendations, report, research, strategies, tradeplan, trials, valuation)
from cryptoagent.collectors import COLLECTORS
from cryptoagent.config import load_settings
from cryptoagent.predictions import LedgerError
from cryptoagent.quant import analogs, indicators, ml, patterns, regime, universe
from cryptoagent.quant.backtest import Costs
from cryptoagent.quant.patterns import SpecError
from cryptoagent.quant.series import UnknownMarket, load, load_daily


class UsageError(Exception):
    pass


def _out(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def _jsonable(row) -> dict:
    return {k: (None if isinstance(v, float) and v != v else v) for k, v in row.items()}


def _bar_label(ts, timeframe: str) -> str:
    """Start of the bar: a date for daily/weekly bars, an ISO UTC time for intraday bars."""
    return ts.date().isoformat() if timeframe in ("1d", "1w") else ts.strftime("%Y-%m-%dT%H:%MZ")


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
    p.add_argument("--timeframe", choices=["1h", "4h", "1d", "1w"], default="1d")

    p = cmd.add_parser("patterns", help="which defined setups are active on the latest bar, with their history")
    p.add_argument("market", help="SOURCE:SYMBOL")
    p.add_argument("--as-of", help="YYYY-MM-DD: use only candles up to this day")
    p.add_argument("--timeframe", choices=["1h", "4h", "1d", "1w"], default="1d")

    p = cmd.add_parser("backtest", help="backtest one setup on a chronological period; always recorded as a trial")
    p.add_argument("--market", required=True,
                   help="SOURCE:SYMBOL, or universe:universe-v2 to pool the setup over every member market")
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
    p.add_argument("--market", help="SOURCE:SYMBOL")
    p.add_argument("--limit", type=int, default=50)
    p = preds.add_parser("score", help="Brier, skill vs base rate, calibration, sample sizes")
    p.add_argument("--market")

    cmd.add_parser("evaluate", help="resolve predictions and recommendations whose horizon day's candle is stored")

    p = cmd.add_parser("scan", help=f"{universe.VERSION} universe with per-market facts (no ranking judgment)")
    p.add_argument("--as-of", help="YYYY-MM-DD: scan as it could have been done at the end of that day")
    p.add_argument("--sort", default="rs90", help="numeric field to sort by, descending (default rs90)")
    p.add_argument("--limit", type=int)
    p.add_argument("--pattern", help="only markets where this pattern is active")
    p.add_argument("--universe", choices=universe.VERSIONS, default=universe.VERSION,
                   help="universe definition (default: the latest approved)")

    an = cmd.add_parser("analogs", help=f"historical analogues ({analogs.VERSION})").add_subparsers(
        dest="action", required=True)
    p = an.add_parser("search", help="past days most like the latest (or --as-of) day, and what followed")
    p.add_argument("market", help="SOURCE:SYMBOL")
    p.add_argument("--as-of", help="YYYY-MM-DD: search as of the end of that day")
    p.add_argument("--horizon", type=int, default=30, help="days ahead (default 30)")
    p.add_argument("--k", type=int, default=15, help="neighbours, each a separate episode (default 15)")
    p.add_argument("--brief", action="store_true", help="omit each neighbour's full state")
    p = an.add_parser("evaluate", help="walk-forward Brier of analogues vs the base rate; always recorded as a trial")
    p.add_argument("--market", required=True)
    p.add_argument("--horizon", type=int, default=30)
    p.add_argument("--k", type=int, default=15)
    p.add_argument("--period", required=True, choices=list(trials.PERIODS))
    p.add_argument("--family", required=True)
    p.add_argument("--hypothesis", required=True)
    p.add_argument("--by", required=True)

    st = cmd.add_parser("strategy", help=f"strategy lifecycle ({strategies.POLICY})").add_subparsers(
        dest="action", required=True)
    p = st.add_parser("propose", help="propose a backtested spec as a strategy (a new version if the name exists)")
    p.add_argument("--name", required=True)
    p.add_argument("--market", required=True, help="SOURCE:SYMBOL or universe:universe-v2")
    p.add_argument("--pattern", required=True, choices=list(patterns.PATTERNS))
    p.add_argument("--when", action="append", default=[], metavar="'[MARKET] FIELD OP VALUE'")
    p.add_argument("--hold", type=int, required=True)
    p.add_argument("--stop-atr", type=float)
    p.add_argument("--target-atr", type=float)
    p.add_argument("--rationale", required=True, help="why, citing trial ids")
    p.add_argument("--by", required=True, choices=("agent", "user"))
    for action, help_text in (("check", "apply the validation gate to the latest version's stored trials"),
                              ("test", "run the sealed test once (needs the user's authorisation)")):
        p = st.add_parser(action, help=help_text)
        p.add_argument("name")
        if action == "test":
            p.add_argument("--by", required=True, help="who ran it, e.g. claude or user")
    for action in ("authorize-test", "approve", "reject", "retire"):
        p = st.add_parser(action, help="user only")
        p.add_argument("name")
        p.add_argument("--by", required=True, choices=("agent", "user"))
        p.add_argument("--note")
    st.add_parser("list", help="every strategy version with its stage, history and paper record")
    st.add_parser("paper", help="daily: record paper signals, resolve paper trades, apply the review gate")
    p = st.add_parser("signals", help="signals of approved strategies on the latest day")
    p.add_argument("--as-of")

    p = cmd.add_parser("valuation", help="scenario valuation from stated assumptions (conditional, not a forecast)")
    p.add_argument("symbol", help="coin symbol, e.g. AAVE")
    p.add_argument("--method", required=True, choices=("multiple", "reverse", "relative", "history"))
    p.add_argument("--case", default="", help="label, e.g. bear, base, bull")
    p.add_argument("--metric", choices=("fees", "revenue"), default="revenue",
                   help="annual metric for multiple/reverse (default revenue: what can reach holders)")
    p.add_argument("--metric-value", type=float, help="annual metric in USD (default: DefiLlama 30d x 365/30)")
    p.add_argument("--multiple", type=float, help="market cap / annual metric assumed at the horizon")
    p.add_argument("--growth", type=float, help="assumed yearly metric growth, e.g. 0.3 for +30%%")
    p.add_argument("--years", type=float, help="horizon in years")
    p.add_argument("--dilution", type=float, help="yearly circulating-supply growth, e.g. 0.05; state 0 for none")
    p.add_argument("--peer", help="relative: peer symbol, e.g. ETH")
    p.add_argument("--fraction", type=float, help="relative: market cap as a fraction of the peer's, e.g. 0.25")
    p.add_argument("--horizon", type=int, default=365, help="history: days (default 365)")
    p.add_argument("--price", type=float, help="override the live price")
    p.add_argument("--supply", type=float, help="override the live circulating supply")

    p = cmd.add_parser("token", help="live market cap, FDV and supply from CoinGecko (not stored)")
    p.add_argument("symbols", nargs="+", help="coin symbols, e.g. SOL AAVE")

    p = cmd.add_parser("dossier", help="everything stored about one coin: market facts, fundamentals, claims, trials, "
                                       "past calls and their outcomes, reports")
    p.add_argument("symbol", help="coin symbol, e.g. AAVE")

    p = cmd.add_parser("plan", help="long trade plan: levels, reward-to-risk, historical first-touch odds, expectancy")
    p.add_argument("market", help="SOURCE:SYMBOL, e.g. coinbase:BTC/USD")
    p.add_argument("--entry", type=float, help="entry price (default: latest close)")
    p.add_argument("--stop", help="price, atr:K or swing_low (default: swing low, or 2 ATR if price is below it)")
    p.add_argument("--target", action="append", help="price, atr:K, swing_high or r:N; repeatable (default: swing "
                                                    "high if above entry, 2R, 3R)")
    p.add_argument("--horizon", type=int, default=30, help="days the trade may run (default 30)")
    p.add_argument("--as-of", help="YYYY-MM-DD: plan with only the history known then")

    p = cmd.add_parser("backup", help="consistent, integrity-checked copy of the database; keeps the latest N")
    p.add_argument("--dir", help=f"destination (default: CRYPTOAGENT_BACKUP_DIR or {backup.DEFAULT_DIR})")
    p.add_argument("--keep", type=int, default=backup.DEFAULT_KEEP)

    cl = cmd.add_parser("call", help="outside commentators' dated calls, scored against base rates").add_subparsers(
        dest="action", required=True)
    p = cl.add_parser("add", help="record one dated, checkable call from a video, post or article")
    p.add_argument("--creator", required=True, help="channel or author name, used to group the score")
    p.add_argument("--url", required=True, help="link to the source, ideally with a timestamp")
    p.add_argument("--published", required=True, help="YYYY-MM-DD the call was made public")
    p.add_argument("--market", required=True, help="SOURCE:SYMBOL, e.g. coinbase:BTC/USD")
    p.add_argument("--type", dest="call_type", required=True, choices=calls.CALL_TYPES)
    p.add_argument("--target", type=float, help="price, for reach_above / reach_below")
    p.add_argument("--deadline", required=True, help="YYYY-MM-DD by which the call must come true")
    p.add_argument("--quote", help="the call in the creator's words, 25 words at most")
    p.add_argument("--by", required=True, choices=("agent", "user"))
    p = cl.add_parser("list")
    p.add_argument("--creator")
    p.add_argument("--limit", type=int, default=50)
    p = cl.add_parser("score", help="hit rate vs base rate per creator, forward and historical apart")
    p.add_argument("--creator")

    p = cmd.add_parser("digest", help="what changed since the last briefing (resolutions, data, strategies, claims)")
    p.add_argument("--since", help="YYYY-MM-DD (default: the last journal briefing, else 7 days ago)")
    p.add_argument("--ahead", type=int, default=7, help="list calls resolving within this many days")

    jr = cmd.add_parser("journal", help="append-only research journal").add_subparsers(dest="action", required=True)
    p = jr.add_parser("add")
    p.add_argument("--kind", choices=("briefing", "note"), default="note")
    p.add_argument("--by", dest="author", required=True, choices=("agent", "user"))
    p.add_argument("--text", required=True)
    p.add_argument("--report", help="path of the report this entry summarises")
    p.add_argument("--evidence", help="JSON object of ids (predictions, recommendations, trials, claims)")
    p = jr.add_parser("list")
    p.add_argument("--limit", type=int, default=10)

    p = cmd.add_parser("fundamentals", help="protocol fundamentals (DefiLlama snapshots) for a token")
    p.add_argument("symbol", help="token symbol, e.g. AAVE")
    p.add_argument("--as-of", help="YYYY-MM-DD: latest snapshot on or before this day")

    note = cmd.add_parser("note", help="research knowledge base").add_subparsers(dest="action", required=True)
    p = note.add_parser("add", help="record a cited fact or a labelled hypothesis")
    p.add_argument("--topic", required=True, choices=research.TOPICS)
    p.add_argument("--subject", required=True, help="MARKET, or a coin symbol such as ETH")
    p.add_argument("--status", required=True, choices=("fact", "hypothesis"))
    p.add_argument("--text", required=True)
    p.add_argument("--confidence", type=float, help="hypotheses only, 0-1")
    p.add_argument("--cite", action="append", default=[],
                   help="url:https://..., trial:<id>, prediction:<id> or claim:<id>; repeatable")
    p.add_argument("--quote", action="append", default=[],
                   help="short excerpt for the citation in the same position (max 25 words)")
    p.add_argument("--as-of", help="YYYY-MM-DD the statement is true as of (default today)")
    p.add_argument("--review-after", help="YYYY-MM-DD to re-check (fees, policies, unlocks)")
    p.add_argument("--by", dest="created_by", choices=("agent", "user"), default="user")
    p.add_argument("--supersedes", type=int)
    p = note.add_parser("list")
    p.add_argument("--topic", choices=research.TOPICS)
    p.add_argument("--subject")
    p.add_argument("--status", choices=("fact", "hypothesis"))
    p.add_argument("--all", action="store_true", help="include superseded claims")
    p.add_argument("--due", action="store_true", help="only claims whose review date has passed")

    mlp = cmd.add_parser("ml", help="machine-learning baselines, judged against the base rate").add_subparsers(
        dest="action", required=True)
    p = mlp.add_parser("evaluate", help="walk-forward Brier of a model vs the base rate; always recorded as a trial")
    p.add_argument("--market", required=True)
    p.add_argument("--model", required=True, choices=list(ml.MODELS))
    p.add_argument("--horizon", type=int, default=30)
    p.add_argument("--period", required=True, choices=list(trials.PERIODS))
    p.add_argument("--family", required=True)
    p.add_argument("--hypothesis", required=True)
    p.add_argument("--by", required=True)

    rec = cmd.add_parser("recommend", help="record a structured recommendation").add_subparsers(dest="action",
                                                                                                 required=True)
    p = rec.add_parser("add")
    p.add_argument("--market", required=True)
    p.add_argument("--horizon", type=int, required=True, help="days")
    p.add_argument("--do", dest="rec_action", required=True, choices=recommendations.ACTIONS)
    p.add_argument("--entry", required=True, help="entry conditions (or why there is none)")
    p.add_argument("--invalidation", required=True)
    p.add_argument("--stop-atr", type=float, help="required for buy: stop at entry - K x ATR14")
    p.add_argument("--target-atr", type=float)
    p.add_argument("--risks", required=True)
    p.add_argument("--limitations", required=True)
    p.add_argument("--evidence", help="JSON object: trial ids, prediction ids, commands")
    p.add_argument("--prediction", type=int, help="id of the linked judgment prediction")
    p.add_argument("--report", help="path of the research report")
    p.add_argument("--by", required=True)

    recs = cmd.add_parser("recommendations", help="the recommendation ledger").add_subparsers(dest="action",
                                                                                               required=True)
    p = recs.add_parser("list")
    p.add_argument("--status", choices=["open", "resolved", "unresolvable"])
    p.add_argument("--limit", type=int, default=50)
    recs.add_parser("score", help="outcomes by action, with sample sizes")

    p = cmd.add_parser("report", help="write the deterministic daily report to reports/YYYY-MM-DD.md")
    p.add_argument("--dir", default="reports")
    return parser


def run_valuation(conn, settings, args) -> dict:
    """Gather inputs (live CoinGecko price and supply, stored DefiLlama fees/revenue), then compute."""
    symbol = args.symbol.upper()
    sources = {}
    if args.method == "history":
        result = valuation.history(load_daily(conn, f"coinbase:{symbol}/USD"), args.horizon)
        return {"symbol": symbol, "case": args.case or None, **result, "data_sources": {"candles": "coinbase daily"}}
    price, supply, live = args.price, args.supply, {}
    peers = [args.peer.upper()] if args.method == "relative" and args.peer else []
    if price is None or supply is None or peers:
        live = coingecko.token_metrics(settings, [symbol, *peers])
        sources["coingecko"] = f"{live['attribution']}, fetched {live['fetched_at']} (not stored)"
        token = live["tokens"].get(symbol)
        if token is None and (price is None or supply is None):
            raise UsageError(f"CoinGecko has no {symbol}; pass --price and --supply")
        price = price if price is not None else token["price"]
        supply = supply if supply is not None else token["circulating_supply"]
    if args.method == "relative":
        if not args.peer:
            raise UsageError("relative needs --peer and --fraction")
        peer = live["tokens"].get(args.peer.upper())
        if peer is None:
            raise UsageError(f"CoinGecko has no {args.peer}")
        result = valuation.relative(price=price, supply=supply, peer_market_cap=peer["market_cap"],
                                    fraction=args.fraction, peer=args.peer.upper(), dilution=args.dilution,
                                    years=args.years)
    else:
        metric = args.metric_value
        if metric is None:
            fams = fundamentals.for_symbol(conn, symbol)
            flow = fams[0].get(f"{args.metric}_30d") if fams else None
            if not flow:
                raise UsageError(f"no stored {args.metric} for {symbol}: pass --metric-value (annual USD, cited)")
            metric = flow * fundamentals.ANNUALISE_30D
            sources["defillama"] = (f"{args.metric}_30d of family {fams[0]['family']} ({fams[0]['name']}) snapshot "
                                    f"{fams[0]['snapshot_day']}, annualised x365/30; check the family is this project")
        kwargs = dict(price=price, supply=supply, annual_metric=metric, multiple=args.multiple, years=args.years,
                      dilution=args.dilution, metric_name=args.metric)
        result = (valuation.multiple(growth=args.growth, **kwargs) if args.method == "multiple"
                  else valuation.reverse(**kwargs))
    return {"symbol": symbol, "case": args.case or None, **result, "data_sources": sources,
            "label": "conditional scenario from stated assumptions, not a forecast"}


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
               b.history_start_found_at, c.first_ts, c.last_ts, coalesce(c.n, 0) AS candles
        FROM markets m
        JOIN sources s ON s.id = m.source_id
        LEFT JOIN market_backfill b ON b.market_id = m.id AND b.timeframe = '1d'
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
        _out([{"bar": _bar_label(ts, args.timeframe), **_jsonable(row)} for ts, row in frame.iterrows()])
    elif c == "patterns":
        df = load(conn, args.market, args.timeframe, args.as_of)
        _out({"market": args.market, "timeframe": args.timeframe,
              "as_of": _bar_label(df.index[-1], args.timeframe) if len(df) else None, "patterns": patterns.active(df)})
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
        _out(predictions.list_predictions(conn, args.status, args.source, args.limit, args.market))
    elif c == "predictions" and args.action == "score":
        _out(predictions.scorecard(conn, args.market))
    elif c == "evaluate":
        _out({"predictions": predictions.evaluate(conn), "recommendations": recommendations.evaluate(conn),
              "external_calls": calls.resolve(conn)})
    elif c == "scan":
        counts = {r[0]: r[1] for r in conn.execute("SELECT market, count(*) FROM trials GROUP BY market")}
        result = universe.scan(conn, args.as_of, sort=args.sort, trials_by_market=counts, version=args.universe)
        fund = fundamentals.summary_by_symbol(conn, result["as_of"])
        for m in result["markets"]:
            m["fundamentals"] = fund.get(m["market"].split(":", 1)[1].split("/")[0])
        if args.pattern:
            if args.pattern not in patterns.PATTERNS:
                raise UsageError(f"unknown pattern {args.pattern!r}")
            result["markets"] = [m for m in result["markets"] if args.pattern in m["active_patterns"]]
            result["filtered_by_pattern"] = args.pattern
        if args.limit:
            result["markets"] = result["markets"][:args.limit]
        _out(result)
    elif c == "analogs" and args.action == "search":
        try:
            result = analogs.search(load_daily(conn, args.market, args.as_of), args.horizon, args.k)
        except analogs.AnalogError as exc:
            raise UsageError(str(exc)) from exc
        if args.brief:
            for n in result["neighbours"]:
                n.pop("state")
        _out(result)
    elif c == "analogs" and args.action == "evaluate":
        _out(trials.run_analog_trial(conn, args.market, args.horizon, args.k, args.period, args.family,
                                     args.hypothesis, args.by))
    elif c == "strategy" and args.action == "propose":
        spec = {"market": args.market, "pattern": args.pattern, "conditions": [parse_when(w) for w in args.when],
                "exit": {"hold_days": args.hold, "stop_atr": args.stop_atr, "target_atr": args.target_atr}}
        _out(strategies.propose(conn, args.name, spec, args.rationale, args.by))
    elif c == "strategy" and args.action == "check":
        _out(strategies.check(conn, args.name))
    elif c == "strategy" and args.action == "test":
        _out(strategies.run_test(conn, args.name, args.by))
    elif c == "strategy" and args.action == "authorize-test":
        _out(strategies.authorize_test(conn, args.name, args.by, args.note))
    elif c == "strategy" and args.action in ("approve", "reject", "retire"):
        event = {"approve": "approved", "reject": "rejected", "retire": "retired"}[args.action]
        _out(strategies.decide(conn, args.name, event, args.by, args.note))
    elif c == "strategy" and args.action == "list":
        _out(strategies.list_strategies(conn))
    elif c == "strategy" and args.action == "paper":
        _out(strategies.paper_step(conn))
    elif c == "strategy" and args.action == "signals":
        _out(strategies.approved_signals(conn, args.as_of))
    elif c == "valuation":
        _out(run_valuation(conn, settings, args))
    elif c == "token":
        _out(coingecko.token_metrics(settings, args.symbols))
    elif c == "dossier":
        _out(dossier.build(conn, args.symbol))
    elif c == "plan":
        _out(tradeplan.plan(load_daily(conn, args.market, args.as_of), args.stop,
                            tuple(args.target) if args.target else None, args.horizon, args.entry))
    elif c == "backup":
        directory = Path(args.dir or settings.secret("CRYPTOAGENT_BACKUP_DIR").reveal() or backup.DEFAULT_DIR)
        _out(backup.run(conn, directory, args.keep))
    elif c == "call" and args.action == "add":
        _out(calls.record(conn, creator=args.creator, source_url=args.url, published_on=args.published,
                          market=args.market, call_type=args.call_type, deadline=args.deadline, recorded_by=args.by,
                          target=args.target, quote=args.quote))
    elif c == "call" and args.action == "list":
        _out(calls.list_calls(conn, args.creator, args.limit))
    elif c == "call" and args.action == "score":
        _out(calls.score(conn, args.creator))
    elif c == "digest":
        _out(digest.digest(conn, args.since, args.ahead))
    elif c == "journal" and args.action == "add":
        try:
            evidence = json.loads(args.evidence) if args.evidence else None
        except json.JSONDecodeError as exc:
            raise UsageError(f"--evidence is not valid JSON: {exc}") from exc
        _out({"journal_id": digest.add_entry(conn, args.kind, args.author, args.text, args.report, evidence)})
    elif c == "journal" and args.action == "list":
        _out(digest.list_entries(conn, args.limit))
    elif c == "fundamentals":
        rows = fundamentals.for_symbol(conn, args.symbol, args.as_of)
        _out({"symbol": args.symbol.upper(), "families": rows,
              "note": "daily snapshots only, from when tracking started; no back-filled history" if rows
              else "unavailable: no DefiLlama family reports this token (or no snapshot yet)"})
    elif c == "note" and args.action == "add":
        if len(args.quote) > len(args.cite):
            raise UsageError("more --quote values than --cite values")
        citations = [research.parse_citation(t) for t in args.cite]
        for citation, quote in zip(citations, args.quote):
            citation.excerpt = quote or None
        _out({"claim_id": research.add_claim(
            conn, topic=args.topic, subject=args.subject, statement=args.text, status=args.status,
            citations=citations, confidence=args.confidence, as_of=args.as_of, review_after=args.review_after,
            created_by=args.created_by, supersedes_id=args.supersedes)})
    elif c == "note" and args.action == "list":
        _out(research.list_claims(conn, args.topic, args.subject, args.status, include_superseded=args.all,
                                  due_for_review=args.due))
    elif c == "ml" and args.action == "evaluate":
        _out(trials.run_ml_trial(conn, args.market, args.model, args.horizon, args.period, args.family,
                                 args.hypothesis, args.by))
    elif c == "recommend":
        try:
            evidence = json.loads(args.evidence) if args.evidence else None
        except json.JSONDecodeError as exc:
            raise UsageError(f"--evidence is not valid JSON: {exc}") from exc
        _out(recommendations.record(conn, args.market, args.horizon, args.rec_action, args.entry, args.invalidation,
                                    args.risks, args.limitations, args.by, args.stop_atr, args.target_atr, evidence,
                                    args.prediction, args.report))
    elif c == "recommendations" and args.action == "list":
        _out(recommendations.list_recommendations(conn, args.status, args.limit))
    elif c == "recommendations" and args.action == "score":
        _out(recommendations.score(conn))
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
    except (UsageError, UnknownMarket, LedgerError, SpecError, strategies.StrategyError, backup.BackupError,
            tradeplan.PlanError, coingecko.CoinGeckoError, valuation.ValuationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except privacy.PrivacyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except research.ResearchError as exc:
        print("\n".join(f"error: {e}" for e in exc.errors), file=sys.stderr)
        return 2
    except sqlite3.IntegrityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
