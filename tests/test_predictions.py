"""The prediction ledger is how the project learns whether anything works, so it must be
impossible to flatter: no back-dated predictions, no early or invented outcomes, no edits,
base rates that only use the past, and a scorecard that admits when samples are too small."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from cryptoagent import predictions as pr
from cryptoagent import store


@pytest.fixture
def btc(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    market_id = store.get_or_create_market(conn, source_id, "BTC", "USD", "BTC/USD", "BTC-USD")
    run_id = store.start_run(conn, source_id, {})
    return market_id, run_id


def add_days(conn, btc, closes, start="2024-01-01"):
    market_id, run_id = btc
    with conn:
        for day, close in zip(pd.date_range(start, periods=len(closes), freq="D"), closes):
            conn.execute("INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
                         " VALUES (?, '1d', ?, ?, ?, ?, ?, 1, ?)",
                         (market_id, day.strftime("%Y-%m-%dT00:00:00Z"), close, close, close, close, run_id))


def test_prediction_binds_to_latest_candle(conn, btc):
    add_days(conn, btc, [100, 101, 102])
    pid = pr.record(conn, "coinbase:BTC/USD", 7, 0.6, "judgment", "claude", "test")
    row = conn.execute("SELECT as_of, as_of_close, target_day FROM predictions WHERE id = ?", (pid,)).fetchone()
    assert tuple(row) == ("2024-01-03", 102.0, "2024-01-10")


def test_backdating_is_refused_even_with_raw_sql(conn, btc):
    add_days(conn, btc, [100, 101, 102])
    with pytest.raises(sqlite3.IntegrityError, match="latest stored candle"):
        conn.execute("INSERT INTO predictions (market_id, as_of, as_of_close, horizon_days, target_day, prob_up, source,"
                     " model) VALUES (?, '2024-01-01', 100, 7, '2024-01-08', 0.9, 'judgment', 'x')", (btc[0],))
    with pytest.raises(sqlite3.IntegrityError, match="latest stored candle"):  # right day, wrong close
        conn.execute("INSERT INTO predictions (market_id, as_of, as_of_close, horizon_days, target_day, prob_up, source,"
                     " model) VALUES (?, '2024-01-03', 90, 7, '2024-01-10', 0.9, 'judgment', 'x')", (btc[0],))


def test_resolves_exactly_at_horizon_and_never_early(conn, btc):
    add_days(conn, btc, [100, 101, 102])
    up = pr.record(conn, "coinbase:BTC/USD", 2, 0.8, "judgment", "claude")
    flat = pr.record(conn, "coinbase:BTC/USD", 3, 0.8, "judgment", "claude-flat")
    add_days(conn, btc, [150], start="2024-01-04")  # day 1 of 2: target not yet stored
    assert pr.evaluate(conn) == {"resolved": 0, "unresolvable": 0, "waiting": 2}
    with pytest.raises(sqlite3.IntegrityError, match="stored close of the target day"):
        conn.execute("INSERT INTO outcomes (prediction_id, status, target_close, up, brier) VALUES (?, 'resolved',"
                     " 150, 1, 0.04)", (up,))
    add_days(conn, btc, [90, 102], start="2024-01-05")
    assert pr.evaluate(conn)["resolved"] == 2
    outcomes = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT prediction_id, up, round(brier, 6) FROM outcomes")}
    assert outcomes[up] == (0, 0.64)    # 90 < 102: down, (0.8 - 0)^2
    assert outcomes[flat] == (0, 0.64)  # 102 == 102 counts as not up
    assert pr.evaluate(conn) == {"resolved": 0, "unresolvable": 0, "waiting": 0}


def test_missing_target_becomes_unresolvable_after_grace(conn, btc):
    add_days(conn, btc, [100])
    pr.record(conn, "coinbase:BTC/USD", 1, 0.5, "judgment", "claude")
    add_days(conn, btc, [100] * 7, start="2024-01-03")  # 2024-01-02 never stored; latest is target + 7
    assert pr.evaluate(conn)["waiting"] == 1
    add_days(conn, btc, [100], start="2024-01-10")
    assert pr.evaluate(conn)["unresolvable"] == 1


def test_ledger_is_append_only(conn, btc):
    add_days(conn, btc, [100, 101])
    pid = pr.record(conn, "coinbase:BTC/USD", 1, 0.5, "judgment", "claude")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE predictions SET prob_up = 0.9 WHERE id = ?", (pid,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM predictions")


def test_base_rates_use_only_completed_past_windows():
    index = pd.date_range("2020-01-01", periods=400, freq="D")
    # Rises every day for 300 days (close = day + 1), then falls (close = 599 - day). A 30-day
    # base rate as of day 399 sees windows starting on days 0..369. Windows starting on days
    # 0..269 end within the rise (up); those starting on 270..283 end in the fall but still
    # above their start (599 - (s + 30) > s + 1 for s < 284). 284 of 370 are up.
    close = np.r_[np.arange(1.0, 301.0), np.arange(299.0, 199.0, -1)]
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=index)
    rates = pr.base_rates(df, 30)
    assert rates["base_rate_v1"]["windows"] == 370
    assert rates["base_rate_v1"]["prob_up"] == pytest.approx(284 / 370)
    # Adding future days must not change a base rate computed as of day 399.
    longer = pd.concat([df, df.iloc[:50].set_axis(pd.date_range("2021-02-04", periods=50, freq="D"))])
    assert pr.base_rates(longer.loc[:"2021-02-03"], 30) == rates


def test_daily_baselines_are_recorded_once(conn, btc):
    rng = np.random.default_rng(0)
    add_days(conn, btc, list(100 * np.exp(np.cumsum(rng.normal(0, 0.03, 700)))), start="2022-01-01")
    first = pr.record_baselines(conn, "coinbase:BTC/USD", horizons=(7, 30))
    assert {(m["model"], m["horizon_days"]) for m in first} >= {("base_rate_v1", 7), ("base_rate_v1", 30)}
    assert pr.record_baselines(conn, "coinbase:BTC/USD", horizons=(7, 30)) == []


def test_scorecard_reports_skill_and_warns_on_small_samples(conn, btc):
    add_days(conn, btc, [100])
    pr.record(conn, "coinbase:BTC/USD", 1, 0.5, "model", "base_rate_v1")
    pr.record(conn, "coinbase:BTC/USD", 1, 0.9, "judgment", "claude")
    add_days(conn, btc, [110], start="2024-01-02")
    pr.evaluate(conn)
    cards = {c["model"]: c for c in pr.scorecard(conn)}
    assert cards["claude"]["brier"] == pytest.approx(0.01) and cards["base_rate_v1"]["brier"] == pytest.approx(0.25)
    assert cards["claude"]["brier_skill_vs_base_rate"] == pytest.approx(0.96)
    assert "too few" in cards["claude"]["warning"] and cards["claude"]["approx_independent"] == 1
    assert cards["base_rate_v1"]["hit_rate"] is None  # 0.5 makes no call


def test_approx_independent_counts_non_overlapping_windows():
    days = [f"2024-01-{d:02d}" for d in range(1, 31)]
    assert pr._approx_independent(days, 7) == 5  # Jan 1, 8, 15, 22, 29


def test_list_filters_by_market(conn, btc):
    add_days(conn, btc, [100, 101])
    pr.record(conn, "coinbase:BTC/USD", 1, 0.5, "judgment", "claude")
    assert len(pr.list_predictions(conn, market="coinbase:BTC/USD")) == 1
    assert pr.list_predictions(conn, market="coinbase:ETH/USD") == []
