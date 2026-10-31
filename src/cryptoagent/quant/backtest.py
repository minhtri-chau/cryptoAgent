"""Daily-bar backtest of one long-only setup on one market. Pure: data in, results out.

Rules (docs/architecture.md, v0.3):
- a signal at the close of day t buys at the open of t+1; no trade without a next bar
- one position at a time; a new signal is taken only after the previous trade has exited
- exits, first one wins:
  - stop at entry open - stop_atr x ATR14 (ATR as of the signal day). Filled at the stop,
    or at the open if the market opens below it
  - target at entry open + target_atr x ATR14, filled at the target or the open if above
  - if stop and target are both inside one day's range, the stop is assumed (conservative)
  - time exit at the close of day t + hold_days
  - the last bar of the period closes any open trade ('period_end'), so a run never uses
    prices after its period
- costs: slippage moves each fill against us; the fee is charged on both sides
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from cryptoagent.quant import indicators as ind

BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 0  # fixed so every result is reproducible


@dataclass(frozen=True)
class Costs:
    fee: float = 0.006       # per side; Coinbase Advanced tier unconfirmed (docs/data-sources.md)
    slippage: float = 0.002  # per side
    confirmed: bool = False  # True only once the user has confirmed their fee tier

    def scaled(self, factor: float) -> "Costs":
        return Costs(self.fee * factor, self.slippage * factor, self.confirmed)


@dataclass(frozen=True)
class Exit:
    hold_days: int
    stop_atr: float | None = None
    target_atr: float | None = None

    def __post_init__(self):
        if self.hold_days < 1:
            raise ValueError("hold_days must be at least 1")
        for value in (self.stop_atr, self.target_atr):
            if value is not None and value <= 0:
                raise ValueError("stop_atr and target_atr must be positive")


def simulate(df: pd.DataFrame, signal: pd.Series, exit: Exit, costs: Costs, start: pd.Timestamp | None = None,
             atr: pd.Series | None = None) -> tuple[list[dict], pd.Series]:
    """Trades and the daily equity curve (starting at 1.0) from `start` to the last bar of `df`."""
    atr = ind.atr(df, 14) if atr is None else atr
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    sig = signal.reindex(df.index).fillna(False).to_numpy(dtype=bool)
    a = atr.reindex(df.index).to_numpy()
    first = 0 if start is None else int(df.index.searchsorted(start))
    last = len(df) - 1
    trades, entries, exits = [], {}, {}
    i = first
    while i < last:
        if not sig[i] or ((exit.stop_atr or exit.target_atr) and np.isnan(a[i])):
            i += 1
            continue
        j0 = i + 1
        stop = o[j0] - exit.stop_atr * a[i] if exit.stop_atr else None
        target = o[j0] + exit.target_atr * a[i] if exit.target_atr else None
        j_end = min(i + exit.hold_days, last)
        for j in range(j0, j_end + 1):
            if stop is not None and l[j] <= stop:
                fill, reason = min(o[j], stop), "stop"
                break
            if target is not None and h[j] >= target:
                fill, reason = max(o[j], target), "target"
                break
        else:
            j, fill = j_end, c[j_end]
            reason = "time" if i + exit.hold_days <= last else "period_end"
        entry_eff = o[j0] * (1 + costs.slippage)
        exit_eff = fill * (1 - costs.slippage)
        ret = exit_eff * (1 - costs.fee) / (entry_eff * (1 + costs.fee)) - 1
        trades.append({
            "signal_day": df.index[i].date().isoformat(), "entry_day": df.index[j0].date().isoformat(),
            "exit_day": df.index[j].date().isoformat(), "entry": o[j0], "exit": fill, "reason": reason,
            "return": ret, "days_held": j - j0 + 1,
            "mae": l[j0:j + 1].min() / o[j0] - 1, "mfe": h[j0:j + 1].max() / o[j0] - 1,
        })
        entries[j0] = entry_eff * (1 + costs.fee)
        exits[j] = exit_eff * (1 - costs.fee)
        i = j  # a signal on the exit day may open the next trade
    equity = np.empty(last - first + 1)
    cash, units = 1.0, 0.0
    for k, d in enumerate(range(first, last + 1)):
        if d in entries:
            units, cash = cash / entries[d], 0.0
        if d in exits:
            cash, units = units * exits[d], 0.0
        equity[k] = cash + units * c[d]
    return trades, pd.Series(equity, index=df.index[first:])


def _ci90(returns: np.ndarray) -> list[float] | None:
    if len(returns) < 2:
        return None
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    means = rng.choice(returns, size=(BOOTSTRAP_SAMPLES, len(returns)), replace=True).mean(axis=1)
    return [round(float(np.quantile(means, 0.05)), 6), round(float(np.quantile(means, 0.95)), 6)]


def ci90_clustered(returns: np.ndarray, clusters: np.ndarray) -> list[float] | None:
    """CI90 of the mean, resampling whole clusters (e.g. signal weeks) instead of single trades.

    Trades triggered by the same market-wide move are not independent; resampling them one
    by one would make the interval look far narrower than the evidence allows.
    """
    labels = np.unique(clusters)
    if len(labels) < 2:
        return None
    groups = [returns[clusters == c] for c in labels]
    sums = np.array([g.sum() for g in groups])
    counts = np.array([len(g) for g in groups])
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    picks = rng.integers(0, len(labels), size=(BOOTSTRAP_SAMPLES, len(labels)))
    means = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    return [round(float(np.quantile(means, 0.05)), 6), round(float(np.quantile(means, 0.95)), 6)]


def trade_stats(trades: list[dict], clusters: np.ndarray | None = None) -> dict:
    """Trade-level statistics; with `clusters`, the CI90 resamples clusters rather than trades."""
    r = np.array([t["return"] for t in trades])
    out = {"trades": len(trades)}
    if len(r):
        gains, losses = r[r > 0].sum(), -r[r < 0].sum()
        out.update({
            "win_rate": round(float((r > 0).mean()), 4),
            "mean_return": round(float(r.mean()), 6),
            "mean_return_ci90": _ci90(r) if clusters is None else ci90_clustered(r, clusters),
            "median_return": round(float(np.median(r)), 6),
            "best": round(float(r.max()), 6), "worst": round(float(r.min()), 6),
            "profit_factor": round(float(gains / losses), 4) if losses > 0 else None,
            "mean_mae": round(float(np.mean([t["mae"] for t in trades])), 6),
            "mean_mfe": round(float(np.mean([t["mfe"] for t in trades])), 6),
            "mean_days_held": round(float(np.mean([t["days_held"] for t in trades])), 2),
            "exits": {k: sum(1 for t in trades if t["reason"] == k) for k in ("time", "stop", "target", "period_end")},
        })
    return out


def summarize(trades: list[dict], equity: pd.Series) -> dict:
    days = len(equity)
    out = trade_stats(trades)
    out["days"] = days
    held = sum(t["days_held"] for t in trades)
    out["exposure"] = round(held / days, 4) if days else None
    out["total_return"] = round(float(equity.iloc[-1] - 1), 6) if days else None
    out["max_drawdown"] = round(float((equity / equity.cummax() - 1).min()), 6) if days else None
    return out


def forward_series(df: pd.DataFrame, signal: pd.Series, hold_days: int, start: pd.Timestamp | None,
                   eligible: pd.Series | None = None) -> tuple[pd.Series, pd.Series]:
    """Close-to-close returns over hold_days from each day (windows ending inside `df` only), and
    which of those days had a signal. `eligible` limits the days considered (e.g. universe membership)."""
    close = df["close"]
    fwd = close.shift(-hold_days) / close - 1
    keep = fwd.notna() & (fwd.index >= start if start is not None else True)
    if eligible is not None:
        keep &= eligible.reindex(df.index).fillna(False).astype(bool)
    fwd = fwd[keep]
    return fwd, signal.reindex(fwd.index).fillna(False).astype(bool)


def _fwd_stats(x: pd.Series) -> dict:
    return {"n": int(len(x)), "mean": round(float(x.mean()), 6) if len(x) else None,
            "share_up": round(float((x > 0).mean()), 4) if len(x) else None}


def forward_returns(df: pd.DataFrame, signal: pd.Series, hold_days: int, start: pd.Timestamp | None) -> dict:
    """Close-to-close return over hold_days after signal days vs after every day (no costs).

    Only windows that end inside `df` count, so nothing beyond the period is used.
    """
    fwd, on = forward_series(df, signal, hold_days, start)
    return {"hold_days": hold_days, "after_signal": _fwd_stats(fwd[on]), "all_days": _fwd_stats(fwd),
            "note": "overlapping windows: n overstates the independent evidence"}


def windows(trades: list[dict], fwd: pd.Series, on_signal: pd.Series) -> dict:
    """The same fixed specification judged one calendar year at a time (by signal day)."""
    years = sorted({d.year for d in fwd.index} | {int(t["signal_day"][:4]) for t in trades})
    rows = []
    for y in years:
        r = np.array([t["return"] for t in trades if int(t["signal_day"][:4]) == y])
        in_year = fwd.index.year == y
        rows.append({"year": y, "trades": int(len(r)),
                     "mean_return": round(float(r.mean()), 6) if len(r) else None,
                     "win_rate": round(float((r > 0).mean()), 4) if len(r) else None,
                     "forward_after_signal": _fwd_stats(fwd[in_year & on_signal.to_numpy()]),
                     "forward_all_days": _fwd_stats(fwd[in_year])})
    traded = [w for w in rows if w["trades"]]
    summary = {"windows": len(rows), "windows_with_trades": len(traded),
               "share_positive_mean": round(sum(w["mean_return"] > 0 for w in traded) / len(traded), 4)
               if traded else None}
    if traded:
        worst = min(traded, key=lambda w: w["mean_return"])
        best = max(traded, key=lambda w: w["mean_return"])
        summary.update(worst={"year": worst["year"], "mean_return": worst["mean_return"], "trades": worst["trades"]},
                       best={"year": best["year"], "mean_return": best["mean_return"], "trades": best["trades"]})
    return {"summary": summary, "by_year": rows}


def buy_and_hold(df: pd.DataFrame, costs: Costs, start: pd.Timestamp | None) -> dict:
    sub = df if start is None else df[df.index >= start]
    if sub.empty:
        return {"total_return": None}
    entry = sub["open"].iloc[0] * (1 + costs.slippage) * (1 + costs.fee)
    exit_ = sub["close"].iloc[-1] * (1 - costs.slippage) * (1 - costs.fee)
    curve = sub["close"] / sub["close"].iloc[0]
    return {"total_return": round(float(exit_ / entry - 1), 6),
            "max_drawdown": round(float((curve / curve.cummax() - 1).min()), 6)}


def run(df: pd.DataFrame, signal: pd.Series, exit: Exit, costs: Costs, start: pd.Timestamp | None = None) -> dict:
    """Full result: trades under the given costs, cost sensitivity, benchmarks and forward returns."""
    atr = ind.atr(df, 14)
    trades, equity = simulate(df, signal, exit, costs, start, atr)
    result = summarize(trades, equity)
    result["costs"] = asdict(costs)
    result["cost_sensitivity"] = {}
    for label, factor in (("zero_costs", 0.0), ("double_costs", 2.0)):
        t, e = simulate(df, signal, exit, costs.scaled(factor), start, atr)
        s = summarize(t, e)
        result["cost_sensitivity"][label] = {k: s.get(k) for k in ("mean_return", "total_return")}
    result["buy_and_hold"] = buy_and_hold(df, costs, start)
    result["forward_returns"] = forward_returns(df, signal, exit.hold_days, start)
    result["windows"] = windows(trades, *forward_series(df, signal, exit.hold_days, start))
    result["trade_list"] = [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in t.items()} for t in trades]
    return result
