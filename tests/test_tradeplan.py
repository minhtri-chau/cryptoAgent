"""A trade plan's numbers are what the user would act on: the reward-to-risk arithmetic must be
exact, the historical odds must follow the real price path (target vs stop, which came first, with
ties counted against us), only history known at the as-of day may be used, and costs must count."""

import numpy as np
import pandas as pd
import pytest

from cryptoagent import tradeplan
from cryptoagent.quant.backtest import Costs


def bars(rows, start="2024-01-01"):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], dtype=float)
    df["volume"] = 1.0
    df.index = pd.date_range(start, periods=len(df), freq="D")
    return df


def walk(n=600, seed=0):
    close = 100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0.0005, 0.02, n)))
    return bars(np.c_[close, close * 1.01, close * 0.99, close])


def test_first_touch_follows_the_path_and_ties_count_as_stopped():
    #        entry day open 100; ATR 10 -> stop 80 (2 ATR), target 120 (2 ATR)
    target_first = bars([(100, 100, 100, 100), (100, 110, 95, 105), (105, 121, 100, 118), (118, 118, 70, 75)])
    stop_first = bars([(100, 100, 100, 100), (100, 105, 79, 85), (85, 125, 84, 120), (120, 120, 120, 120)])
    tie = bars([(100, 100, 100, 100), (100, 125, 75, 100), (100, 100, 100, 100), (100, 100, 100, 100)])
    neither = bars([(100, 100, 100, 100), (100, 110, 90, 104), (104, 110, 90, 106), (106, 110, 95, 110)])
    atr = lambda df: pd.Series(10.0, index=df.index)  # noqa: E731
    days = np.array([0])
    assert tradeplan.first_touch(target_first, atr(target_first), 2, 2, 3, days)["p_target_first"] == 1
    assert tradeplan.first_touch(stop_first, atr(stop_first), 2, 2, 3, days)["p_stop_first"] == 1
    assert tradeplan.first_touch(tie, atr(tie), 2, 2, 3, days)["p_stop_first"] == 1
    n = tradeplan.first_touch(neither, atr(neither), 2, 2, 3, days)
    assert n["p_neither"] == 1 and n["mean_return_if_neither"] == pytest.approx(0.10)  # 110 / entry 100


def test_reward_to_risk_arithmetic_and_costs():
    df = walk()
    p = tradeplan.plan(df, stop="atr:2", targets=("r:2", "atr:3"), costs=Costs(fee=0.006, slippage=0.002))
    entry, atr = p["entry"], p["atr14"]
    assert p["stop"]["price"] == pytest.approx(entry - 2 * atr)
    two_r, three_atr = p["targets"]
    assert two_r["price"] == pytest.approx(entry + 2 * (2 * atr)) and two_r["reward_to_risk"] == 2.0
    assert three_atr["reward_to_risk"] == pytest.approx(1.5)
    risk, reward, cost = 2 * atr / entry, 4 * atr / entry, 0.016
    assert two_r["reward_to_risk_after_costs"] == pytest.approx((reward - cost) / (risk + cost), abs=1e-3)
    free = tradeplan.plan(df, stop="atr:2", targets=("r:2",), costs=Costs(0, 0))["targets"][0]
    assert free["expectancy_all_days"]["net_pct"] > two_r["expectancy_all_days"]["net_pct"]  # costs bite


def test_uses_only_history_up_to_the_as_of_day():
    df = walk(800, 3)
    cut = df.iloc[:600]
    garbage = df.copy()
    garbage.iloc[600:] = np.random.default_rng(1).uniform(1, 1e5, size=garbage.iloc[600:].shape)
    assert tradeplan.plan(cut) == tradeplan.plan(garbage.iloc[:600])
    odds = tradeplan.plan(cut, horizon=30)["targets"][0]["odds_all_days"]
    assert odds["n"] <= 600 - 30 - 1  # every window, next open to horizon close, lies inside the history


def test_refuses_levels_on_the_wrong_side():
    df = walk()
    entry = float(df["close"].iloc[-1])
    with pytest.raises(tradeplan.PlanError, match="not below entry"):
        tradeplan.plan(df, stop=str(entry * 1.1))
    with pytest.raises(tradeplan.PlanError, match="not above entry"):
        tradeplan.plan(df, stop="atr:2", targets=(str(entry * 0.9),))
