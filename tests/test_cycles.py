"""Timing claims from outside analysts (cycle lows N days after the peak, lows after midterm
elections, long stretches below the 200-day average) are checked with `cycles`. Each number
must mean exactly what its definition says, use only data up to the as-of day, and show how
few past episodes it rests on."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from cryptoagent.quant import cycles


def series(values, start="2014-01-01"):
    return pd.Series(np.asarray(values, dtype=float), index=pd.date_range(start, periods=len(values), freq="D"))


@pytest.mark.parametrize("year,day", [(2014, date(2014, 11, 4)), (2018, date(2018, 11, 6)),
                                      (2022, date(2022, 11, 8)), (2026, date(2026, 11, 3))])
def test_midterm_election_is_tuesday_after_first_monday(year, day):
    assert cycles.midterm_election_day(year) == day


def test_cycle_needs_the_drawdown_and_a_new_high_to_be_complete():
    # peak 100 on day 10, low 40 on day 50 (-60%), new high on day 80; then a 30% dip that doesn't count
    path = np.r_[np.linspace(50, 100, 11), np.linspace(99, 40, 40), np.linspace(41, 120, 30), np.linspace(119, 84, 20)]
    rows = cycles.cycles(series(path), min_drawdown=0.5)
    assert rows[0] == {"peak_day": "2014-01-11", "peak_close": 100.0, "low_day": "2014-02-20", "low_close": 40.0,
                       "days_peak_to_low": 40, "drawdown": -0.6, "complete": True}
    assert rows[1]["complete"] is False and rows[1]["low_close"] == 84.0  # ongoing: lowest so far, not a cycle low


def test_sma200_runs_report_the_low_and_wait_for_365_days():
    path = np.r_[np.full(250, 100.0), np.linspace(99, 50, 220), np.linspace(51, 200, 80)]
    runs = cycles.sma200_runs(series(path), min_days=180)
    assert len(runs) == 1
    run = runs[0]
    assert run["days_below"] >= 180 and run["run_low_close"] == 50.0 and run["sma200_falling"]
    assert run["low_broken_within_365d"] is None and run["return_365d"] is None and "pending" in run["note"]


def test_report_uses_only_data_up_to_the_last_day():
    rng = np.random.default_rng(4)
    close = series(100 * np.exp(np.cumsum(rng.normal(0.001, 0.04, 1500))))
    cut = 1100
    reference = cycles.report(close.iloc[:cut + 1].to_frame("close"))
    garbage = close.copy()
    garbage.iloc[cut + 1:] = rng.uniform(1, 1e6, len(close) - cut - 1)
    # Any row not marked pending or incomplete in the reference must be identical with any future.
    future = cycles.report(garbage.to_frame("close").iloc[:cut + 1])
    assert future == reference
    full = cycles.report(garbage.to_frame("close"))
    done = [r for r in reference["cycles"] if r["complete"]]
    assert full["cycles"][:len(done)] == done
