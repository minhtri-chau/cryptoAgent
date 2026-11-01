"""Side-by-side facts for several coins on identical criteria, so a comparison is consistent and
reproducible. No scores and no ranking: weighing the criteria is reasoning, done in /compare.

Per coin (all from stored data, plus live CoinGecko figures when given, which are never stored):
- structure: close, trend label, distance from SMA200 / EMA200, drawdown from the high, RSI,
  ATR % and 30-day volatility
- momentum: returns over 30 / 90 / 365 days and 90-day strength relative to BTC
- liquidity and universe membership
- fundamentals: DefiLlama family, TVL, annualised fees and revenue; with a live market cap,
  market cap / annualised revenue; dilution (FDV / market cap, circulating share)
- history: the coin's own return distribution over the horizon, with the independent sample size
- plan: default long plan's stop distance and the 2R target's historical odds and expectancy
- evidence on record: trials, and the agent's judgment predictions
Plus the correlation of daily returns between the coins over the last 365 days.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from cryptoagent import fundamentals, tradeplan, valuation
from cryptoagent.quant import indicators as ind
from cryptoagent.quant import regime, universe
from cryptoagent.quant.series import UnknownMarket, load_daily

CORRELATION_DAYS = 365


def _r(value, digits=6):
    if value is None:
        return None
    value = float(value)
    return None if value != value else round(value, digits)


def _coin(conn, symbol: str, horizon: int, token: dict | None, btc_roc90: float | None) -> tuple[dict, pd.Series | None]:
    market = f"coinbase:{symbol}/USD"
    try:
        df = load_daily(conn, market)
    except UnknownMarket:
        return {"symbol": symbol, "available": False, "note": f"no {market} candles stored"}, None
    if len(df) < 250:
        return {"symbol": symbol, "available": False, "note": f"only {len(df)} days of history"}, None
    i = ind.compute_all(df).iloc[-1]
    trend = regime.classify(df)["trend"].iloc[-1]
    member, reason, facts = universe.membership(df, df.index[-1], symbol)
    out = {
        "symbol": symbol, "available": True, "market": market, "as_of": df.index[-1].date().isoformat(),
        "structure": {"close": _r(i["close"], 8), "trend": trend if isinstance(trend, str) else None,
                      "sma200_deviation": _r(i["close"] / i["sma200"] - 1), "ema200_deviation": _r(i["ema200_dev"]),
                      "drawdown_from_high": _r(i["drawdown_ath"]), "rsi14": _r(i["rsi14"], 2),
                      "atr14_pct": _r(i["atr14_pct"]), "vol30": _r(i["vol30"])},
        "momentum": {"return_30d": _r(i["roc30"]), "return_90d": _r(i["roc90"]), "return_365d": _r(i["roc365"]),
                     "strength_vs_btc_90d": _r((1 + i["roc90"]) / (1 + btc_roc90) - 1)
                     if btc_roc90 is not None and symbol != "BTC" else None},
        "liquidity": {"universe_member": member, "reason": reason,
                      "median_dollar_volume_30d": facts.get("median_dollar_volume"), "history_days": len(df)},
    }
    fams = fundamentals.for_symbol(conn, symbol)
    top = fams[0] if fams else None
    annual_rev = top["revenue_30d"] * fundamentals.ANNUALISE_30D if top and top.get("revenue_30d") else None
    annual_fees = top["fees_30d"] * fundamentals.ANNUALISE_30D if top and top.get("fees_30d") else None
    mcap = token.get("market_cap") if token else None
    out["fundamentals"] = {
        "defillama_family": f"{top['family']} ({top['name']})" if top else None,
        "tvl": _r(top["tvl"], 0) if top else None, "annual_fees": _r(annual_fees, 0), "annual_revenue": _r(annual_rev, 0),
        "market_cap": mcap, "market_cap_to_revenue": _r(mcap / annual_rev, 2) if mcap and annual_rev else None,
        "fdv_to_market_cap": token.get("fdv_to_market_cap") if token else None,
        "circulating_share_of_max": token.get("circulating_share_of_max") if token else None,
        "note": None if top else "no DefiLlama family for this token (L1 chains and non-DeFi tokens usually have none)",
    }
    if top and not top["tvl"]:
        out["fundamentals"]["note"] = ("DefiLlama family has zero TVL: its fees/revenue may be chain-level or "
                                       "mis-attributed, so market_cap_to_revenue is not comparable with DeFi "
                                       "protocols; verify with the fundamental-analyst")
        out["fundamentals"]["market_cap_to_revenue_comparable"] = False
    try:
        h = valuation.history(df, horizon)
        out["history"] = {"horizon_days": horizon, **h["quantiles"], "share_positive": h["share_positive"],
                          "approx_independent": h["approx_independent"]}
    except valuation.ValuationError as exc:
        out["history"] = {"unavailable": str(exc)}
    try:
        p = tradeplan.plan(df, targets=("r:2",), horizon=min(horizon, 365))
        t = p["targets"][0]
        out["plan_2r"] = {"stop": p["stop"]["spec"], "stop_pct": p["stop"]["pct_from_entry"],
                          "p_target_first": t["odds_all_days"].get("p_target_first"),
                          "p_stop_first": t["odds_all_days"].get("p_stop_first"),
                          "expectancy_net_r": (t["expectancy_all_days"] or {}).get("net_r"),
                          "approx_independent": t["odds_all_days"].get("approx_independent")}
    except tradeplan.PlanError as exc:
        out["plan_2r"] = {"unavailable": str(exc)}
    trials = conn.execute("SELECT count(*) FROM trials WHERE market = ?", (market,)).fetchone()[0]
    judged = conn.execute("SELECT count(*), sum(o.prediction_id IS NOT NULL) FROM predictions p JOIN markets m"
                          " ON m.id = p.market_id JOIN sources s ON s.id = m.source_id LEFT JOIN outcomes o"
                          " ON o.prediction_id = p.id WHERE s.name || ':' || m.symbol = ? AND p.source = 'judgment'",
                          (market,)).fetchone()
    out["record"] = {"trials_on_this_coin": trials, "judgment_predictions": judged[0], "resolved": judged[1] or 0}
    returns = np.log(df["close"]).diff().iloc[-CORRELATION_DAYS:]
    return out, returns.rename(symbol)


def compare(conn: sqlite3.Connection, symbols: list[str], horizon: int = 365, tokens: dict | None = None) -> dict:
    symbols = list(dict.fromkeys(s.upper() for s in symbols))
    if not 2 <= len(symbols) <= 6:
        raise ValueError("compare 2 to 6 coins")
    tokens = tokens or {}
    try:
        btc = ind.compute_all(load_daily(conn, "coinbase:BTC/USD")).iloc[-1]["roc90"]
    except UnknownMarket:
        btc = None
    coins, series = [], []
    for s in symbols:
        coin, returns = _coin(conn, s, horizon, tokens.get(s), btc)
        coins.append(coin)
        if returns is not None:
            series.append(returns)
    corr = None
    if len(series) >= 2:
        frame = pd.concat(series, axis=1, join="inner").dropna()
        corr = {"days": len(frame), "matrix": {a: {b: _r(frame[a].corr(frame[b]), 3) for b in frame.columns}
                                               for a in frame.columns}}
    return {"horizon_days": horizon, "coins": coins, "correlation_daily_returns": corr,
            "notes": ["facts only: no scores and no ranking", "history and plan odds are base rates, not forecasts",
                      "market cap and dilution are live CoinGecko figures when present, never stored"]}
