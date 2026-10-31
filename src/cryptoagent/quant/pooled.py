"""Pooled backtest: one fixed specification across every market of a universe.

Single coins rarely have enough history for a fair test (most altcoins listed after 2021).
Pooling the same rule across the whole universe gives far more trades, at a price: trades
triggered by one market-wide move are not independent. So:
- each market is simulated on its own (one position at a time per market), and a signal
  counts only on days that market was a universe member, judged point-in-time
- statistics are trade-level; the CI90 of the mean resamples whole signal weeks, and the
  number of distinct signal weeks is reported next to the trade count
- there is no portfolio equity curve: sizing and concurrency are a strategy choice for later
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from cryptoagent.quant import backtest
from cryptoagent.quant.backtest import Costs, Exit
from cryptoagent.quant import indicators as ind


def _week(day: str) -> str:
    iso = pd.Timestamp(day).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def run(frames: dict[str, pd.DataFrame], signal_for: Callable[[str, pd.DataFrame], pd.Series],
        member_for: Callable[[str, pd.DataFrame], pd.Series], exit: Exit, costs: Costs,
        start: pd.Timestamp | None = None) -> dict:
    """`frames` maps market -> daily candles (already cut at the period end)."""
    prepared = {}
    for market, df in frames.items():
        if len(df) < 2 or (start is not None and df.index[-1] < start):
            continue
        member = member_for(market, df)
        signal = signal_for(market, df) & member
        prepared[market] = (df, signal, member, ind.atr(df, 14))

    def simulate_all(c: Costs) -> list[dict]:
        trades = []
        for market, (df, signal, _, atr) in prepared.items():
            if signal.any():
                ts, _ = backtest.simulate(df, signal, exit, c, start, atr)
                trades += [t | {"market": market} for t in ts]
        return trades

    trades = simulate_all(costs)
    weeks = np.array([_week(t["signal_day"]) for t in trades])
    result = backtest.trade_stats(trades, clusters=weeks)
    result["distinct_signal_weeks"] = int(len(set(weeks)))
    result["markets_with_signals"] = len({t["market"] for t in trades})
    result["markets_considered"] = len(prepared)
    if trades:
        events = sorted([(t["entry_day"], 1) for t in trades] + [(t["exit_day"], -1) for t in trades],
                        key=lambda e: (e[0], -e[1]))
        level = peak = 0
        for _, step in events:
            level += step
            peak = max(peak, level)
        result["max_concurrent_trades"] = peak
    result["costs"] = {"fee": costs.fee, "slippage": costs.slippage, "confirmed": costs.confirmed}
    result["cost_sensitivity"] = {}
    for label, factor in (("zero_costs", 0.0), ("double_costs", 2.0)):
        s = backtest.trade_stats(simulate_all(costs.scaled(factor)))
        result["cost_sensitivity"][label] = {"mean_return": s.get("mean_return")}

    fwd_parts, on_parts = [], []
    for df, signal, member, _ in prepared.values():
        fwd, on = backtest.forward_series(df, signal, exit.hold_days, start, eligible=member)
        fwd_parts.append(fwd)
        on_parts.append(on)
    fwd = pd.concat(fwd_parts) if fwd_parts else pd.Series(dtype=float)
    on = pd.concat(on_parts) if on_parts else pd.Series(dtype=bool)
    result["forward_returns"] = {
        "hold_days": exit.hold_days, "after_signal": backtest._fwd_stats(fwd[on.to_numpy()]),
        "all_member_days": backtest._fwd_stats(fwd),
        "note": "pooled over markets and overlapping windows: n overstates the independent evidence"}
    result["windows"] = backtest.windows(trades, fwd, on)
    result["trade_list"] = [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in t.items()} for t in trades]
    return result
