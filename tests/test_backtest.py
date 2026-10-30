"""The backtest engine decides whether a hypothesis looks profitable, so its fills must be
ones that could really have happened: entry at the next open, stops filled at the worse
price when the market gaps, costs on both sides, no overlapping positions, and nothing
priced after the data ends."""

import numpy as np
import pandas as pd
import pytest

from cryptoagent.quant import backtest as bt
from cryptoagent.quant.backtest import Costs, Exit

FREE = Costs(0.0, 0.0)


def bars(rows, start="2024-01-01"):
    """rows of (open, high, low, close)."""
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], dtype=float)
    df["volume"] = 1.0
    df.index = pd.date_range(start, periods=len(df), freq="D")
    return df


def signal_on(df, *days):
    s = pd.Series(False, index=df.index)
    s.iloc[list(days)] = True
    return s


def flat_atr(df, value=1.0):
    return pd.Series(value, index=df.index)


def test_enters_next_open_and_exits_at_close_after_hold():
    df = bars([(10, 10, 10, 10), (11, 12, 11, 12), (12, 13, 12, 13), (13, 14, 13, 14)])
    trades, equity = bt.simulate(df, signal_on(df, 0), Exit(hold_days=2), FREE)
    t = trades[0]
    assert (t["entry_day"], t["entry"], t["exit_day"], t["exit"], t["reason"]) == \
        ("2024-01-02", 11.0, "2024-01-03", 13.0, "time")
    assert t["return"] == pytest.approx(13 / 11 - 1)
    assert equity.iloc[-1] == pytest.approx(13 / 11)


def test_costs_are_charged_on_both_sides():
    df = bars([(100, 100, 100, 100), (100, 100, 100, 100), (110, 110, 110, 110)])
    trades, _ = bt.simulate(df, signal_on(df, 0), Exit(hold_days=2), Costs(fee=0.01, slippage=0.005))
    expected = (110 * 0.995 * 0.99) / (100 * 1.005 * 1.01) - 1
    assert trades[0]["return"] == pytest.approx(expected)


def test_stop_fills_at_stop_or_at_a_gapped_open():
    # Stop = entry 100 - 2 x ATR(5) = 90.
    normal = bars([(100, 100, 100, 100), (100, 101, 95, 96), (96, 97, 89, 92), (92, 92, 92, 92)])
    gapped = bars([(100, 100, 100, 100), (100, 101, 95, 96), (80, 82, 78, 81), (81, 81, 81, 81)])
    for df, fill in ((normal, 90.0), (gapped, 80.0)):
        trades, _ = bt.simulate(df, signal_on(df, 0), Exit(hold_days=3, stop_atr=2), FREE, atr=flat_atr(df, 5))
        assert (trades[0]["reason"], trades[0]["exit"], trades[0]["exit_day"]) == ("stop", fill, "2024-01-03")


def test_stop_wins_when_stop_and_target_share_a_bar():
    df = bars([(100, 100, 100, 100), (100, 120, 80, 100), (100, 100, 100, 100)])
    trades, _ = bt.simulate(df, signal_on(df, 0), Exit(hold_days=2, stop_atr=1, target_atr=1), FREE,
                            atr=flat_atr(df, 10))
    assert (trades[0]["reason"], trades[0]["exit"]) == ("stop", 90.0)


def test_target_fill():
    df = bars([(100, 100, 100, 100), (100, 105, 99, 104), (112, 115, 110, 114), (114, 114, 114, 114)])
    trades, _ = bt.simulate(df, signal_on(df, 0), Exit(hold_days=3, target_atr=1), FREE, atr=flat_atr(df, 10))
    assert (trades[0]["reason"], trades[0]["exit"]) == ("target", 112.0)  # opened above the 110 target


def test_one_position_at_a_time_and_no_entry_without_a_next_bar():
    df = bars([(10, 10, 10, 10)] * 8)
    trades, _ = bt.simulate(df, signal_on(df, 0, 1, 2, 3, 7), Exit(hold_days=3), FREE)
    # Signal 0 holds days 1..3; signals 1 and 2 are ignored; signal 3 (the exit day) re-enters on day 4.
    assert [t["signal_day"] for t in trades] == ["2024-01-01", "2024-01-04"]
    assert trades[-1]["exit_day"] == "2024-01-07"  # day 7's signal has no next bar


def test_period_end_closes_open_trade_without_later_prices():
    df = bars([(10, 10, 10, 10), (10, 11, 10, 11), (11, 12, 11, 12)])
    trades, _ = bt.simulate(df, signal_on(df, 0), Exit(hold_days=10), FREE)
    assert (trades[0]["reason"], trades[0]["exit_day"], trades[0]["exit"]) == ("period_end", "2024-01-03", 12.0)


def test_signals_before_start_are_ignored():
    df = bars([(10, 10, 10, 10)] * 6)
    trades, equity = bt.simulate(df, signal_on(df, 0, 3), Exit(hold_days=1), FREE, start=df.index[2])
    assert [t["signal_day"] for t in trades] == ["2024-01-04"] and equity.index[0] == df.index[2]


def test_summary_and_benchmarks():
    rng = np.random.default_rng(4)
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 400)))
    df = bars(np.c_[close, close * 1.01, close * 0.99, close])
    signal = pd.Series(np.arange(400) % 20 == 0, index=df.index)
    result = bt.run(df, signal, Exit(hold_days=5), Costs())
    assert result["trades"] == 20 and len(result["trade_list"]) == 20
    lo, hi = result["mean_return_ci90"]
    assert lo <= result["mean_return"] <= hi
    sens = result["cost_sensitivity"]
    assert sens["zero_costs"]["mean_return"] > result["mean_return"] > sens["double_costs"]["mean_return"]
    assert result["forward_returns"]["after_signal"]["n"] == 20
    assert result["buy_and_hold"]["total_return"] == pytest.approx(
        close[-1] * 0.998 * 0.994 / (close[0] * 1.002 * 1.006) - 1)
