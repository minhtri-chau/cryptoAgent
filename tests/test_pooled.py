"""Pooled tests exist because single altcoins have too little history for a fair test. They
are only an improvement if pooling doesn't smuggle in new biases: a coin may only trade on
days it was really in the universe (judged with data up to that day), dead coins must count,
the period boundary must hold, and trades set off by one market-wide move must not be
counted as independent evidence."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from cryptoagent import store, trials
from cryptoagent.quant import backtest, pooled, universe
from cryptoagent.quant.backtest import Costs, Exit


def frame(close, volume, start="2020-01-01"):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": np.broadcast_to(volume, close.shape).astype(float)},
                        index=pd.date_range(start, periods=len(close), freq="D"))


def walk(n, seed, sigma=0.04):
    return 10 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, sigma, n)))


@pytest.mark.parametrize("version", universe.VERSIONS)
def test_member_mask_matches_membership_day_by_day(version):
    """The vectorised mask is used for backtests, membership() for scans: they must agree."""
    cases = {
        "ALT": frame(walk(500, 1), np.r_[np.full(300, 10.0), np.full(200, 1_000_000.0)]),  # illiquid, then liquid
        "PEG": frame(np.r_[1 + np.random.default_rng(2).normal(0, 0.0005, 350), walk(150, 3, 0.05) / 10],
                     5_000_000.0),  # stablecoin-like, then the peg breaks
        "USDT": frame(1 + np.random.default_rng(4).normal(0, 0.0005, 400), 5_000_000.0),  # listed exclusion
    }
    for base, df in cases.items():
        mask = universe.member_mask(df, base, version)
        for t in range(0, len(df), 7):
            expected = universe.membership(df.iloc[:t + 1], df.index[t], base, version)[0]
            assert mask.iloc[t] == expected, (base, version, df.index[t])


def test_window_breakdown_by_year():
    trades = [{"signal_day": "2021-03-01", "return": 0.10}, {"signal_day": "2021-06-01", "return": -0.04},
              {"signal_day": "2022-02-01", "return": -0.02}]
    days = pd.date_range("2021-01-01", "2022-12-31", freq="D")
    fwd = pd.Series(np.where(days.year == 2021, 0.05, -0.01), index=days)
    on = pd.Series(days.isin(pd.to_datetime(["2021-03-01", "2021-06-01", "2022-02-01"])), index=days)
    w = backtest.windows(trades, fwd, on)
    by = {r["year"]: r for r in w["by_year"]}
    assert (by[2021]["trades"], by[2021]["mean_return"], by[2022]["mean_return"]) == (2, 0.03, -0.02)
    assert by[2022]["forward_all_days"]["mean"] == -0.01
    assert w["summary"] == {"windows": 2, "windows_with_trades": 2, "share_positive_mean": 0.5,
                            "worst": {"year": 2022, "mean_return": -0.02, "trades": 1},
                            "best": {"year": 2021, "mean_return": 0.03, "trades": 2}}


def test_clustered_ci_does_not_treat_one_market_wide_move_as_many_samples():
    rng = np.random.default_rng(0)
    week_returns = rng.normal(0.0, 0.05, 20)
    returns = np.repeat(week_returns, 10)  # 10 coins move together each week
    weeks = np.repeat(np.arange(20), 10)
    naive = backtest._ci90(returns)
    clustered = backtest.ci90_clustered(returns, weeks)
    assert (clustered[1] - clustered[0]) > 2 * (naive[1] - naive[0])
    assert backtest.ci90_clustered(returns[:10], weeks[:10]) is None  # one week is one observation


def test_pooled_trades_only_member_days_includes_dead_coins_and_respects_the_period():
    n = 400
    frames = {
        "x:A/USD": frame(walk(n, 5), 1_000_000.0),
        "x:B/USD": frame(walk(n, 6), np.r_[np.full(300, 1.0), np.full(100, 1_000_000.0)]),  # liquid only from day 300
        "x:DEAD/USD": frame(walk(330, 7), 1_000_000.0),  # stops trading on day 329
    }
    every_tenth = lambda m, df: pd.Series(np.arange(len(df)) % 10 == 0, index=df.index)  # noqa: E731
    member = lambda m, df: universe.member_mask(df, m.split(":")[1].split("/")[0], "universe-v1")  # noqa: E731
    result = pooled.run(frames, every_tenth, member, Exit(hold_days=5), Costs(0, 0), start=pd.Timestamp("2020-06-01"))
    by_market = {}
    for t in result["trade_list"]:
        by_market.setdefault(t["market"], []).append(t)
    assert set(by_market) == {"x:A/USD", "x:B/USD", "x:DEAD/USD"}  # the dead coin's past trades count
    for market, trades in by_market.items():  # every signal fell on a day the coin was a member
        mask = member(market, frames[market])
        assert all(mask[pd.Timestamp(t["signal_day"])] for t in trades)
    assert min(t["signal_day"] for t in by_market["x:B/USD"]) > frames["x:B/USD"].index[300].date().isoformat()
    assert max(t["exit_day"] for t in by_market["x:DEAD/USD"]) <= frames["x:DEAD/USD"].index[-1].date().isoformat()
    assert all(t["signal_day"] >= "2020-06-01" for t in result["trade_list"])
    for trades in by_market.values():  # one position at a time per market
        spans = sorted((t["entry_day"], t["exit_day"]) for t in trades)
        assert all(prev[1] <= nxt[0] for prev, nxt in zip(spans, spans[1:]))
    assert result["max_concurrent_trades"] >= 2 and result["distinct_signal_weeks"] <= result["trades"]
    returns = np.array([t["return"] for t in result["trade_list"]])  # the CI resamples weeks, not trades
    weeks = np.array([pooled._week(t["signal_day"]) for t in result["trade_list"]])
    assert result["mean_return_ci90"] == backtest.ci90_clustered(returns, weeks)
    assert result["mean_return_ci90"] != backtest._ci90(returns)


def test_pooled_spec_is_a_sealed_trial(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    run_id = store.start_run(conn, source_id, {})
    days = pd.date_range("2020-01-01", "2024-12-31", freq="D")
    for i, symbol in enumerate(("BTC/USD", "ALT/USD", "OLD/USD")):
        market_id = store.get_or_create_market(conn, source_id, symbol.split("/")[0], "USD", symbol, symbol)
        span = days if symbol != "OLD/USD" else days[:1200]  # OLD delisted in 2023
        close = walk(len(span), 10 + i, 0.03) * 100
        with conn:
            conn.executemany(
                "INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                " VALUES (?, '1d', ?, ?, ?, ?, ?, 100000, ?)",
                [(market_id, d.strftime("%Y-%m-%dT00:00:00Z"), c, c * 1.02, c * 0.98, c, run_id)
                 for d, c in zip(span, close)])
    spec = {"market": "universe:universe-v2", "pattern": "breakout_20",
            "conditions": [{"market": "coinbase:BTC/USD", "field": "trend", "op": "==", "value": "uptrend"}],
            "exit": {"hold_days": 10, "stop_atr": 2}}
    with pytest.raises(sqlite3.IntegrityError, match="validation trial"):
        trials.run_trial(conn, spec, "test", "pooled-breakout", "h", "t")
    val = trials.run_trial(conn, spec, "validation", "pooled-breakout", "h", "t", include_trades=True)
    assert val["markets_considered"] == 3 and val["trades"] > 0
    assert max(t["exit_day"] for t in val["trade_list"]) <= "2024-06-30"
    assert val["windows"]["summary"]["windows"] >= 2
    with pytest.raises(trials.SpecError, match="unknown universe"):
        trials.canonical(dict(spec, market="universe:everything"))
