"""accumulate-v1: how accumulation rules for a fixed amount of capital would have played out.

A rule turns 1.0 unit of cash into a position over time. Four kinds:
- lump:   everything at the first opportunity
- time:   `tranches` equal weekly buys
- ladder: `levels` below the start price, each buying `weight` of the starting capital once its
          level is reached; optional `deadline_weeks` buys whatever is left
- hybrid: `initial` bought at once, plus ladder `levels`, plus a `rally` trigger (a close at or
          above start x (1 + up)) that switches to buying the remainder over `rally_tranches`
          weekly buys, plus `deadline_weeks` / `deadline_tranches` for whatever is still left
Levels are {"drop": d} (d below the start close) or {"above_low": x} (x above the lowest close
since the last all-time high, as known at the start).

Execution: every decision uses the close of day t and fills at the open of t+1, paying slippage
and the fee (`backtest.Costs`); levels trigger on closes, not intraday lows. Cash earns
`cash_yield` a year, compounded daily. Nothing after day t is used to decide day t.

Each start day is also labelled by what the next 60 closes did (an outcome label, used only to
group results): A = a close below the lowest close since the last all-time high and at least 15%
below the start; B = a close at least 15% below the start but not A; C = neither.
Results are descriptive history over overlapping start dates, not forecasts.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from cryptoagent.quant.backtest import Costs
from cryptoagent.quant.patterns import SpecError

VERSION = "accumulate-v1"
KINDS = ("lump", "time", "ladder", "hybrid")
LABEL_DAYS = 60
LABEL_DROP = 0.15
RALLY_UP = 0.25           # "missed rally": a close this far above the start within RALLY_DAYS ...
RALLY_DAYS = 84
MISSED_BELOW = 0.5        # ... while less than this share of the capital was deployed
DEFAULT_WEIGHTS = {"A": 0.25, "B": 0.20, "C": 0.55}


def _levels(raw) -> list[dict]:
    out = []
    for lv in raw or []:
        if not isinstance(lv, dict) or "weight" not in lv or len(set(lv) & {"drop", "above_low"}) != 1:
            raise SpecError(f"each level needs a weight and exactly one of drop / above_low: {lv}")
        if not 0 < float(lv["weight"]) <= 1:
            raise SpecError("level weights must be in (0, 1]")
        out.append({k: float(v) for k, v in lv.items()})
    return out


def normalise(rule: dict) -> dict:
    """Validate a rule and return it in canonical form (so equal rules hash equally)."""
    if not isinstance(rule, dict) or rule.get("kind") not in KINDS:
        raise SpecError(f"rule needs kind in {', '.join(KINDS)}")
    kind = rule["kind"]
    allowed = {"lump": {"kind"}, "time": {"kind", "tranches"},
               "ladder": {"kind", "levels", "deadline_weeks"},
               "hybrid": {"kind", "initial", "levels", "rally_up", "rally_tranches", "deadline_weeks",
                          "deadline_tranches"}}[kind]
    if set(rule) - allowed:
        raise SpecError(f"unknown keys for {kind}: {sorted(set(rule) - allowed)}")
    norm = {"kind": kind}
    if kind == "time":
        norm["tranches"] = int(rule.get("tranches", 12))
        if norm["tranches"] < 1:
            raise SpecError("tranches must be at least 1")
    if kind in ("ladder", "hybrid"):
        norm["levels"] = _levels(rule.get("levels"))
        norm["deadline_weeks"] = int(rule["deadline_weeks"]) if rule.get("deadline_weeks") is not None else None
    if kind == "hybrid":
        norm["initial"] = float(rule.get("initial", 0))
        norm["rally_up"] = float(rule["rally_up"]) if rule.get("rally_up") is not None else None
        norm["rally_tranches"] = int(rule.get("rally_tranches", 4))
        norm["deadline_tranches"] = int(rule.get("deadline_tranches", 1))
        if not 0 <= norm["initial"] <= 1:
            raise SpecError("initial must be between 0 and 1")
    if kind in ("ladder", "hybrid"):
        total = sum(lv["weight"] for lv in norm["levels"]) + norm.get("initial", 0)
        if total > 1 + 1e-9:
            raise SpecError(f"initial plus level weights is {total:.2f}, more than the capital")
    return norm


def cycle_low(close: np.ndarray) -> np.ndarray:
    """Lowest close since the most recent all-time-high close, as known on each day."""
    out = np.empty_like(close)
    ath = -math.inf
    for i, c in enumerate(close):
        if c >= ath:
            ath, out[i] = c, c
        else:
            out[i] = min(out[i - 1], c)
    return out


def label(close: np.ndarray, low: np.ndarray, t0: int) -> str | None:
    if t0 + LABEL_DAYS >= len(close):
        return None
    m = close[t0 + 1:t0 + 1 + LABEL_DAYS].min()
    deep = m <= close[t0] * (1 - LABEL_DROP)
    if deep and m < low[t0]:
        return "A"
    return "B" if deep else "C"


def simulate(df: pd.DataFrame, t0: int, rule: dict, costs: Costs, cash_yield: float, days: int,
             low: np.ndarray | None = None) -> dict:
    """One rule from start position t0 over `days` days (needs t0 + days inside df)."""
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    low = cycle_low(c) if low is None else low
    start = c[t0]
    daily = (1 + cash_yield) ** (1 / 365) - 1
    cash, btc, spent, pending = 1.0, 0.0, 0.0, 0.0
    filled = [False] * len(rule.get("levels", []))
    mode, mode_k, left = None, 0, 0     # "rally" or "deadline": weekly tranches from day mode_k
    deployed, value = {}, []
    for k in range(days + 1):
        i = t0 + k
        if k:
            cash *= 1 + daily
        if k and pending > 0:   # fill yesterday's decision at today's open
            amount = min(pending, cash)
            btc += amount / (o[i] * (1 + costs.slippage) * (1 + costs.fee))
            cash -= amount
            spent += amount
        value.append(cash + btc * c[i])
        deployed[k] = spent
        pending = 0.0
        if k == days:
            break
        kind = rule["kind"]
        if kind == "lump" and k == 0:
            pending = cash
        elif kind == "time" and k % 7 == 0 and k // 7 < rule["tranches"]:
            pending = cash / (rule["tranches"] - k // 7)
        elif kind in ("ladder", "hybrid"):
            if kind == "hybrid" and k == 0:
                pending += rule["initial"]
            for j, lv in enumerate(rule["levels"]):
                price = start * (1 - lv["drop"]) if "drop" in lv else low[t0] * (1 + lv["above_low"])
                if not filled[j] and c[i] <= price:
                    filled[j] = True
                    pending += lv["weight"]
            if kind == "hybrid" and mode is None and rule["rally_up"] is not None and c[i] >= start * (1 + rule["rally_up"]):
                mode, mode_k, left, filled = "rally", k, rule["rally_tranches"], [True] * len(filled)
            if mode is None and rule["deadline_weeks"] is not None and k == 7 * rule["deadline_weeks"]:
                mode, mode_k, left, filled = "deadline", k, rule.get("deadline_tranches", 1), [True] * len(filled)
            if mode and left and (k - mode_k) % 7 == 0:   # equal shares of what's left, the last takes it all
                pending += (cash - pending) / left
                left -= 1
        pending = min(pending, cash)
    v = np.array(value)
    rally_k = next((k for k in range(1, min(RALLY_DAYS, days) + 1) if c[t0 + k] >= start * (1 + RALLY_UP)), None)
    return {"value": float(v[-1]), "avg_price_ratio": (spent / btc) / start if btc else None,
            "deployed_4w": deployed[min(28, days)], "deployed_12w": deployed[min(84, days)],
            "deployed_24w": deployed[min(168, days)], "max_drawdown": float((v / np.maximum.accumulate(v) - 1).min()),
            "rally": rally_k is not None,
            "missed_rally": rally_k is not None and deployed[rally_k] < MISSED_BELOW}


def _q(x: list[float]) -> dict:
    x = [v for v in x if v is not None]
    if not x:
        return {"n": 0}
    a = np.array(x)
    return {"n": len(a), "p10": round(float(np.quantile(a, .1)), 4), "median": round(float(np.median(a)), 4),
            "p90": round(float(np.quantile(a, .9)), 4), "mean": round(float(a.mean()), 4)}


def evaluate(df: pd.DataFrame, rule: dict, start: pd.Timestamp | None, costs: Costs = Costs(),
             cash_yield: float = 0.0, horizons: tuple[int, ...] = (365, 730), step: int = 7,
             weights: dict | None = None) -> dict:
    """Run `rule` and the lump-sum benchmark from every `step`-th start day whose shortest horizon
    fits inside `df` (the caller truncates df to the period end, so nothing beyond it is read)."""
    rule = normalise(rule)
    c = df["close"].to_numpy()
    low = cycle_low(c)
    first = 0 if start is None else int(df.index.searchsorted(start))
    rows = []
    for t0 in range(first, len(df) - min(horizons), step):
        lab = label(c, low, t0)
        if lab is None:
            continue
        row = {"day": df.index[t0].date().isoformat(), "label": lab}
        for h in horizons:
            if t0 + h >= len(df):
                continue
            mine = simulate(df, t0, rule, costs, cash_yield, h, low)
            lump = simulate(df, t0, {"kind": "lump"}, costs, cash_yield, h, low)
            row[h] = mine | {"return": mine["value"] - 1, "vs_lump": mine["value"] / lump["value"] - 1}
        rows.append(row)
    if not rows:
        raise SpecError("no start dates fit in this period")

    def summary(sub: list[dict]) -> dict:
        out = {"starts": len(sub)}
        for h in horizons:
            got = [r[h] for r in sub if h in r]
            if not got:
                continue
            out[f"{h}d"] = {"return": _q([g["return"] for g in got]), "vs_lump": _q([g["vs_lump"] for g in got]),
                            "max_drawdown": _q([g["max_drawdown"] for g in got]),
                            "avg_price_ratio": _q([g["avg_price_ratio"] for g in got])}
        first_h = min(horizons)
        got = [r[first_h] for r in sub if first_h in r]
        out["deployed"] = {w: _q([g[f"deployed_{w}"] for g in got]) for w in ("4w", "12w", "24w")}
        rallies = [g for g in got if g["rally"]]
        out["missed_rally"] = {"share_of_all_starts": round(sum(g["missed_rally"] for g in got) / len(got), 4),
                               "share_of_rallies": round(sum(g["missed_rally"] for g in rallies) / len(rallies), 4)
                               if rallies else None, "rallies": len(rallies)}
        return out

    by = {lab: summary([r for r in rows if r["label"] == lab]) for lab in "ABC" if any(r["label"] == lab for r in rows)}
    weights = weights or DEFAULT_WEIGHTS
    weighted = {}
    for a in (0.10, weights["A"], 0.40):
        rest = (1 - a) / (weights["B"] + weights["C"])
        w = {"A": a, "B": weights["B"] * rest, "C": weights["C"] * rest}
        cell = {}
        for h in horizons:
            key = f"{h}d"
            if all(key in by.get(lab, {}) for lab in "ABC"):
                cell[key] = {m: round(sum(w[lab] * by[lab][key][m]["median"] for lab in "ABC"), 4)
                             for m in ("return", "vs_lump", "max_drawdown")}
        weighted[f"A={a:.2f}"] = {"weights": {k: round(v, 3) for k, v in w.items()}, **cell}
    days_span = (pd.Timestamp(rows[-1]["day"]) - pd.Timestamp(rows[0]["day"])).days
    return {"version": VERSION, "rule": rule, "costs": {"fee": costs.fee, "slippage": costs.slippage,
                                                         "confirmed": costs.confirmed},
            "cash_yield": cash_yield, "first_day": rows[0]["day"], "last_start": rows[-1]["day"],
            "evaluations": days_span // 365 + 1, "starts": len(rows), "step_days": step,
            "overall": summary(rows), "by_scenario": by, "probability_weighted_medians": weighted,
            "dates": [r["day"] for r in rows],
            "notes": ["overlapping start dates: 'evaluations' (non-overlapping years) is the effective sample",
                      "scenario labels use the next 60 closes and only group results",
                      "probability-weighted values are weighted medians of each scenario's results, not forecasts"]}
