"""Deterministic daily report (no AI): data freshness, BTC regime and indicators, today's
baseline predictions and the scorecard. Written to reports/YYYY-MM-DD.md."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptoagent import predictions, quality, recommendations
from cryptoagent.quant import indicators, regime, universe
from cryptoagent.quant.series import UnknownMarket, load_daily

REGIME_MARKET = "bitstamp:BTC/USD"   # one venue back to 2011, for cycle context
TRADE_MARKET = "coinbase:BTC/USD"    # the tradable series predictions are scored on

INDICATOR_ROWS = [
    ("close", "Close", "price"), ("sma50", "SMA 50", "price"), ("sma200", "SMA 200", "price"),
    ("ema200_dev", "Deviation from EMA 200", "pct"), ("rsi14", "RSI 14", "num"), ("macd_hist", "MACD histogram", "num"),
    ("adx", "ADX 14", "num"), ("atr14_pct", "ATR 14 (% of close)", "level"), ("vol30", "30-day realised vol (annualised)", "level"),
    ("roc7", "Return 7d", "pct"), ("roc30", "Return 30d", "pct"), ("roc90", "Return 90d", "pct"),
    ("roc365", "Return 365d", "pct"), ("drawdown_ath", "Drawdown from all-time high", "pct"),
    ("pivot_high", "Last confirmed swing high", "price"), ("pivot_low", "Last confirmed swing low", "price"),
]


def _fmt(value, kind="num") -> str:
    if value is None or value != value:
        return "unavailable"
    if kind == "pct":
        return f"{value:+.1%}"
    if kind == "level":
        return f"{value:.1%}"
    if kind == "price":
        return f"{value:,.2f}"
    return f"{value:,.2f}" if isinstance(value, float) else str(value)


def _data_section(conn) -> list[str]:
    rows = conn.execute(
        "SELECT s.name, (SELECT status || ' at ' || coalesce(finished_at, started_at) FROM ingest_runs"
        "  WHERE source_id = s.id ORDER BY id DESC LIMIT 1) AS last_run,"
        " (SELECT count(*) FROM markets WHERE source_id = s.id) AS markets,"
        " coalesce((SELECT substr(max(c.ts), 1, 10) FROM candles c JOIN markets m ON m.id = c.market_id"
        "  WHERE m.source_id = s.id AND c.timeframe = '1d'),"
        " (SELECT max(day) FROM reference_prices WHERE source_id = s.id)) AS latest"
        " FROM sources s ORDER BY s.name"
    ).fetchall()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    lines = ["## Data", "", "| Source | Latest day | Markets | Last run |", "|---|---|---|---|"]
    stale = []
    for name, last_run, markets, latest in rows:
        lines.append(f"| {name} | {latest or 'none'} | {markets} | {last_run or 'never'} |")
        if not latest or latest < yesterday:
            stale.append(name)
    if stale:
        lines += ["", f"**Stale:** {', '.join(stale)} not updated through {yesterday} (UTC)."]
    totals = quality.summary(conn, limit=0)["totals"]
    new = conn.execute("SELECT check_name, count(*) FROM quality_issues WHERE detected_at >= ?"
                       " GROUP BY 1", ((datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),)
                       ).fetchall()
    lines += ["", "Quality issues on record: " + (", ".join(f"{k} {v}" for k, v in sorted(totals.items())) or "none")
              + ". New in the last 24 h: " + (", ".join(f"{k} {v}" for k, v in new) or "none") + "."]
    return lines


def _regime_section(conn) -> list[str]:
    try:
        snap = regime.snapshot(load_daily(conn, REGIME_MARKET))
    except UnknownMarket:
        return ["## Bitcoin regime", "", f"unavailable: no data for {REGIME_MARKET}"]
    band = snap["ema200_band"] or "none reached"
    return [
        f"## Bitcoin regime ({snap['version']}, {REGIME_MARKET}, as of {snap['as_of']})", "",
        "Descriptive labels with fixed definitions (see `src/cryptoagent/quant/regime.py`); not trade signals.", "",
        "| Field | Value |", "|---|---|",
        f"| Trend (close vs SMA 200, SMA 200 20-day change) | **{snap['trend'] or 'unavailable'}** "
        f"({_fmt(snap['close'], 'price')} vs {_fmt(snap['sma200'], 'price')}; {_fmt(snap['sma200_change20'], 'pct')}) |",
        f"| Volatility (30-day vs all earlier days) | **{snap['volatility'] or 'unavailable'}** "
        f"({_fmt(snap['vol30'], 'level')}, percentile {_fmt(snap['vol30_percentile'], 'level')}) |",
        f"| Drawdown from all-time high | {_fmt(snap['drawdown_ath'], 'pct')} |",
        f"| Deviation from EMA 200 / deepest band | {_fmt(snap['ema200_dev'], 'pct')} / {band} |",
        f"| Halving cycle | cycle {snap['halving_cycle']}, day {snap['days_since_halving']} |",
    ]


def _indicator_section(conn) -> list[str]:
    try:
        df = load_daily(conn, TRADE_MARKET)
    except UnknownMarket:
        return ["## Bitcoin indicators", "", f"unavailable: no data for {TRADE_MARKET}"]
    last = indicators.compute_all(df).iloc[-1]
    lines = [f"## Bitcoin indicators ({TRADE_MARKET}, as of {df.index[-1].date()})", "", "| Indicator | Value |", "|---|---|"]
    lines += [f"| {label} | {_fmt(last[col], kind)} |" for col, label, kind in INDICATOR_ROWS]
    return lines


def _predictions_section(conn) -> list[str]:
    made = [p for p in predictions.list_predictions(conn, limit=200) if p["source"] == "model"]
    latest = max((p["as_of"] for p in made), default=None)
    lines = ["## Baseline predictions", ""]
    if latest is None:
        return lines + ["None recorded yet."]
    lines += [f"P(close on target day > close on {latest}), from base rates of past windows (no judgment). "
              "They reflect this market's own history, which includes strong upward drift: a benchmark that "
              "any judgment or strategy has to beat, not a forecast that the drift continues.", "",
              "| Model | Horizon | Target day | P(up) |", "|---|---|---|---|"]
    for p in sorted((p for p in made if p["as_of"] == latest), key=lambda p: (p["horizon_days"], p["model"])):
        lines.append(f"| {p['model']} | {p['horizon_days']}d | {p['target_day']} | {p['prob_up']:.1%} |")
    return lines


def _scorecard_section(conn) -> list[str]:
    cards = predictions.scorecard(conn)
    lines = ["## Scorecard", ""]
    if not cards:
        return lines + ["No predictions yet."]
    lines += ["| Model | Horizon | Resolved (≈independent) | Open | Brier | Skill vs base rate | Hit rate |",
              "|---|---|---|---|---|---|---|"]
    for c in cards:
        lines.append(f"| {c['source']}:{c['model']} | {c['horizon_days']}d | {c['resolved']} "
                     f"({c.get('approx_independent', 0)}) | {c['open']} | {_fmt(c.get('brier'))} | "
                     f"{_fmt(c.get('brier_skill_vs_base_rate'))} | {_fmt(c.get('hit_rate'), 'level')} |")
    if any("warning" in c for c in cards):
        lines += ["", f"Rows with fewer than {predictions.SMALL_SAMPLE} non-overlapping outcomes are too small to "
                      "support any conclusion."]
    return lines


def _scanner_section(conn) -> list[str]:
    result = universe.scan(conn)
    c = result["counts"]
    lines = [f"## Scanner ({result['version']}, as of {result['as_of']})", "",
             f"{c['universe']} liquid markets out of {c['markets_with_history']} with history "
             f"(stale or delisted {c['stale']}, under {universe.MIN_HISTORY_DAYS} days {c['short_history']}, "
             f"median 30-day dollar volume under ${universe.MIN_MEDIAN_DOLLAR_VOLUME:,.0f} {c['illiquid']}).", ""]
    with_setups = [m for m in result["markets"] if m["active_patterns"]]
    if not with_setups:
        return lines + ["No defined setup is active in the universe today."]
    lines += ["Markets with an active setup, by 90-day return relative to BTC. Facts only: a setup here is untested "
              "for that market until `/investigate` says otherwise.", "",
              "| Market | Setups | RS 90d vs BTC | RS 30d | Trend | Dev. from SMA200 | Median $ volume |",
              "|---|---|---|---|---|---|---|"]
    for m in with_setups[:15]:
        lines.append(f"| {m['market']} | {', '.join(m['active_patterns'])} | {_fmt(m['rs90'], 'pct')} | "
                     f"{_fmt(m['rs30'], 'pct')} | {m['trend'] or 'unavailable'} | {_fmt(m['sma200_dev'], 'pct')} | "
                     f"{m['median_dollar_volume']:,.0f} |")
    return lines


def _recommendations_section(conn) -> list[str]:
    scores = recommendations.score(conn)
    lines = ["## Recommendations", ""]
    if not scores:
        return lines + ["None recorded yet."]
    lines += ["| Action | Recorded | Open | Resolved | Mean market return | Mean trade return (buys) |",
              "|---|---|---|---|---|---|"]
    for s in scores:
        lines.append(f"| {s['action']} | {s['recommendations']} | {s['open']} | {s['resolved']} | "
                     f"{_fmt(s.get('mean_market_return'), 'pct')} | {_fmt(s.get('mean_trade_return'), 'pct')} |")
    return lines


def render(conn: sqlite3.Connection) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sections = [
        [f"# Daily market report", "", f"Generated {now}. Deterministic output: every number comes from stored data "
         "and the code in this repository; there is no AI interpretation here. Not financial advice."],
        _data_section(conn), _regime_section(conn), _indicator_section(conn), _predictions_section(conn),
        _scorecard_section(conn), _recommendations_section(conn), _scanner_section(conn),
        ["## Sources", "", "Reference prices: Coin Metrics Community (CC BY-NC 4.0). Candles: Coinbase Exchange and "
         "Bitstamp public market data."],
    ]
    return "\n\n".join("\n".join(s) for s in sections) + "\n"


def write(conn: sqlite3.Connection, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now().date().isoformat()}.md"
    path.write_text(render(conn), encoding="utf-8")
    return path
