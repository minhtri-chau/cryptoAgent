"""Trade plans: entry, stop and targets turned into risk/reward and historical first-touch odds.

Answers "what is the risk to reward of buying X now?" with numbers, not narrative:
- levels: stop and targets as prices, as % from entry, and in ATR14 multiples; R:R per target
  ((target - entry) / (entry - stop)), before and after round-trip costs
- historical odds, volatility-adjusted: on every past day s whose whole window ended by the as-of
  day, a long entry at the next open with the stop and target at the SAME ATR multiples (using
  ATR on day s). Which was touched first within the horizon: target, stop, or neither (time exit
  at the horizon close)? A day whose range touches both counts as the stop (conservative).
- the same odds restricted to days in the same regime-v1 trend as the as-of day
- expectancy per trade from those odds, after costs, in % and in R

These are base rates from this market's own history, which includes its long-run drift: context
for a decision, not a forecast and not a validated strategy. Long only, no position sizing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cryptoagent.quant import indicators as ind
from cryptoagent.quant import regime
from cryptoagent.quant.backtest import Costs

MIN_WINDOWS = 30


class PlanError(ValueError):
    pass


def _level(spec: str, kind: str, entry: float, atr: float, swing_low: float | None, swing_high: float | None,
           stop: float | None = None) -> float:
    """A level from a price ('70000'), 'atr:K', 'swing_low' / 'swing_high', or (targets only) 'r:N'."""
    spec = str(spec).strip().lower()
    if spec in ("swing_low", "swing_high"):
        value = swing_low if spec == "swing_low" else swing_high
        if value is None or value != value:
            raise PlanError(f"no confirmed {spec.replace('_', ' ')} yet for this market")
        return float(value)
    if spec.startswith("atr:"):
        k = float(spec[4:])
        return entry - k * atr if kind == "stop" else entry + k * atr
    if spec.startswith("r:"):
        if kind == "stop" or stop is None:
            raise PlanError("r:N is only for targets: N times the distance from entry to stop")
        return entry + float(spec[2:]) * (entry - stop)
    try:
        return float(spec)
    except ValueError as exc:
        raise PlanError(f"cannot read level {spec!r}: use a price, atr:K, swing_low, swing_high or r:N") from exc


def first_touch(df: pd.DataFrame, atr: pd.Series, stop_atr: float, target_atr: float, horizon: int,
                days: np.ndarray) -> dict:
    """Outcome counts for long entries at the next open after each position in `days`."""
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    a = atr.to_numpy(dtype=float)
    target_hits = stop_hits = 0
    neither = []
    for s in days:
        if np.isnan(a[s]):
            continue
        entry = o[s + 1]
        stop, target = entry - stop_atr * a[s], entry + target_atr * a[s]
        outcome = None
        for j in range(s + 1, s + horizon + 1):
            if l[j] <= stop:
                outcome = "stop"
                break
            if h[j] >= target:
                outcome = "target"
                break
        if outcome == "target":
            target_hits += 1
        elif outcome == "stop":
            stop_hits += 1
        else:
            neither.append(c[s + horizon] / entry - 1)
    n = target_hits + stop_hits + len(neither)
    if n == 0:
        return {"n": 0}
    return {"n": n, "approx_independent": n // horizon,
            "p_target_first": round(target_hits / n, 4), "p_stop_first": round(stop_hits / n, 4),
            "p_neither": round(len(neither) / n, 4),
            "mean_return_if_neither": round(float(np.mean(neither)), 6) if neither else None}


def _expectancy(odds: dict, target_pct: float, stop_pct: float, cost: float, risk_pct: float) -> dict | None:
    if odds.get("n", 0) < MIN_WINDOWS:
        return None
    gross = (odds["p_target_first"] * target_pct + odds["p_stop_first"] * stop_pct
             + odds["p_neither"] * (odds["mean_return_if_neither"] or 0.0))
    net = gross - cost
    return {"gross_pct": round(gross, 6), "net_pct": round(net, 6), "net_r": round(net / risk_pct, 4)}


def plan(df: pd.DataFrame, stop: str | None = None, targets: tuple[str, ...] | None = None,
         horizon: int = 30, entry: float | None = None, costs: Costs = Costs()) -> dict:
    """A long plan as of the last day of `df` (only `df` is used, so `df` cut at a date is point-in-time)."""
    if len(df) < 250:
        raise PlanError("need at least 250 days of history for levels and odds")
    i = ind.compute_all(df)
    last = i.iloc[-1]
    atr = float(last["atr14"])
    entry = float(last["close"]) if entry is None else float(entry)
    notes = []
    if stop is None:  # default: the last confirmed swing low, unless price has already broken below it
        below = last["pivot_low"] == last["pivot_low"] and last["pivot_low"] < entry
        stop = "swing_low" if below else "atr:2"
        if not below:
            notes.append("price is below the last confirmed swing low (structure broken): default stop is 2 x ATR14")
    stop_price = _level(stop, "stop", entry, atr, last["pivot_low"], last["pivot_high"])
    if stop_price >= entry:
        raise PlanError(f"stop {stop_price:.6g} is not below entry {entry:.6g} (price is already under that level)")
    risk = entry - stop_price
    risk_pct = risk / entry
    cost = 2 * (costs.fee + costs.slippage)  # round trip, both sides
    trend = regime.classify(df)["trend"]
    today_trend = trend.iloc[-1] if isinstance(trend.iloc[-1], str) else None
    atr_series = ind.atr(df, 14)
    eligible = np.arange(0, len(df) - horizon - 1)  # whole window (next open .. horizon close) inside df
    same = eligible[(trend.iloc[eligible] == today_trend).to_numpy()] if today_trend else np.array([], dtype=int)
    if not targets:  # default: the last swing high when it is above entry, plus 2R and 3R
        above = last["pivot_high"] == last["pivot_high"] and last["pivot_high"] > entry
        targets = (("swing_high",) if above else ()) + ("r:2", "r:3")
    out_targets = []
    for spec in targets:
        price = _level(spec, "target", entry, atr, last["pivot_low"], last["pivot_high"], stop_price)
        if price <= entry:
            raise PlanError(f"target {spec} = {price:.6g} is not above entry {entry:.6g}")
        reward = price - entry
        stop_atr, target_atr = risk / atr, reward / atr
        overall = first_touch(df, atr_series, stop_atr, target_atr, horizon, eligible)
        regime_odds = first_touch(df, atr_series, stop_atr, target_atr, horizon, same) if len(same) else {"n": 0}
        out_targets.append({
            "spec": spec, "price": round(price, 8), "pct_from_entry": round(reward / entry, 6),
            "atr_multiple": round(target_atr, 3),
            "reward_to_risk": round(reward / risk, 3),
            "reward_to_risk_after_costs": round((reward / entry - cost) / (risk_pct + cost), 3),
            "odds_all_days": overall,
            "odds_same_trend": regime_odds,
            "expectancy_all_days": _expectancy(overall, reward / entry, -risk_pct, cost, risk_pct),
            "expectancy_same_trend": _expectancy(regime_odds, reward / entry, -risk_pct, cost, risk_pct),
        })
    return {
        "as_of": df.index[-1].date().isoformat(), "horizon_days": horizon, "side": "long",
        "entry": round(entry, 8), "atr14": round(atr, 8), "trend": today_trend,
        "stop": {"spec": stop, "price": round(stop_price, 8), "pct_from_entry": round(-risk_pct, 6),
                 "atr_multiple": round(risk / atr, 3)},
        "swing_low": None if last["pivot_low"] != last["pivot_low"] else float(last["pivot_low"]),
        "swing_high": None if last["pivot_high"] != last["pivot_high"] else float(last["pivot_high"]),
        "round_trip_cost_pct": round(cost, 6), "costs_confirmed": costs.confirmed,
        "targets": out_targets,
        "notes": notes + [
            "odds are base rates from this market's history (volatility-adjusted via ATR), not a forecast",
            "overlapping windows: approx_independent is the honest sample size",
            "a day touching both stop and target counts as stopped out",
        ] + (["costs are placeholders until the Coinbase fee tier is confirmed"] if not costs.confirmed else []),
    }
