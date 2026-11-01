"""Descriptive cycle history of one market: the checks /learn-from needs for timing claims.

Read-only and deterministic, computed from daily closes up to the as-of day (no look-ahead):
- cycles: each all-time-high peak followed by a drawdown of at least `min_drawdown`, the
  lowest close before the next new high, and the days between. The last cycle is marked
  incomplete while no new high has been made; its "low" is the lowest close so far.
- sma200_runs: stretches of at least `min_days` consecutive closes below the 200-day SMA,
  the reclaim day, whether SMA200 was falling then, and whether the stretch's low was broken
  in the following 365 days (pending while fewer than 365 days have passed).
- midterms: US midterm election days in the data (the Tuesday after the first Monday in
  November of 2010, 2014, ...), each year's lowest close in Q1, June-September and Q4, the
  next cycle low after the election, and the change 365 days after the election.
These are descriptive facts about a handful of cycles, not forecasts; `n` is always reported.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from cryptoagent.quant import indicators as ind


def midterm_election_day(year: int) -> date:
    """US federal elections: the Tuesday after the first Monday in November."""
    first = date(year, 11, 1)
    first_monday = first + timedelta(days=(0 - first.weekday()) % 7)
    return first_monday + timedelta(days=1)


def _day(ts) -> str:
    return ts.date().isoformat()


def cycles(close: pd.Series, min_drawdown: float = 0.5) -> list[dict]:
    out, peak_day, peak = [], close.index[0], close.iloc[0]
    low_day, low = peak_day, peak
    for day, value in close.items():
        if value > peak:
            if low / peak - 1 <= -min_drawdown:
                out.append({"peak_day": _day(peak_day), "peak_close": peak, "low_day": _day(low_day), "low_close": low,
                            "days_peak_to_low": (low_day - peak_day).days, "drawdown": round(low / peak - 1, 4),
                            "complete": True})
            peak_day, peak, low_day, low = day, value, day, value
        elif value < low:
            low_day, low = day, value
    if low / peak - 1 <= -min_drawdown or out:
        out.append({"peak_day": _day(peak_day), "peak_close": peak, "low_day": _day(low_day), "low_close": low,
                    "days_peak_to_low": (low_day - peak_day).days, "drawdown": round(low / peak - 1, 4),
                    "complete": False, "days_since_peak": (close.index[-1] - peak_day).days,
                    "note": "no new high yet: the low is the lowest close so far"})
    return out


def sma200_runs(close: pd.Series, min_days: int = 180) -> list[dict]:
    i = ind.compute_all(pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 0.0}))
    below = ind.run_length(close < i["sma200"])
    out = []
    for pos in range(1, len(close)):
        if below.iloc[pos] == 0 and below.iloc[pos - 1] >= min_days:
            run = int(below.iloc[pos - 1])
            run_low = close.iloc[pos - run:pos]
            after = close.iloc[pos:pos + 366]
            days_after = len(after) - 1
            row = {"reclaim_day": _day(close.index[pos]), "days_below": run, "run_start": _day(close.index[pos - run]),
                   "sma200_falling": bool(i["sma200_change20"].iloc[pos] < 0),
                   "run_low_day": _day(run_low.idxmin()), "run_low_close": float(run_low.min()),
                   "low_broken_within_365d": bool(after.min() < run_low.min()) if days_after >= 365 or
                   after.min() < run_low.min() else None}
            row["return_365d"] = round(float(after.iloc[365] / after.iloc[0] - 1), 4) if days_after >= 365 else None
            if days_after < 365:
                row["note"] = f"pending: {days_after} of 365 days observed"
            out.append(row)
    return out


def midterms(close: pd.Series, cycle_rows: list[dict]) -> list[dict]:
    out = []
    first, last = close.index[0].date(), close.index[-1].date()
    for year in range(2010, last.year + 1, 4):
        election = midterm_election_day(year)
        if election < first or election > last:
            continue
        def low(a: str, b: str):
            s = close[a:b]
            return {"day": _day(s.idxmin()), "close": float(s.min())} if len(s) else None
        windows = {"q1": low(f"{year}-01-01", f"{year}-03-31"), "jun_sep": low(f"{year}-06-01", f"{year}-09-30"),
                   "q4": low(f"{year}-10-01", f"{year}-12-31")}
        after = [c for c in cycle_rows if c["low_day"] >= election.isoformat()]
        on = close[:election.isoformat()]
        end = close[:(election + timedelta(days=365)).isoformat()]
        row = {"year": year, "election_day": election.isoformat(), "election_close": float(on.iloc[-1]),
               "lowest_close": windows,
               "q4_below_summer_below_q1": bool(windows["q1"] and windows["jun_sep"] and windows["q4"] and
                                                windows["q4"]["close"] < windows["jun_sep"]["close"] < windows["q1"]["close"]),
               "next_cycle_low": ({"day": after[0]["low_day"], "days_after_election":
                                   (date.fromisoformat(after[0]["low_day"]) - election).days,
                                   "complete": after[0]["complete"]} if after else None)}
        if election + timedelta(days=365) <= last:
            row["change_365d"] = round(float(end.iloc[-1] / on.iloc[-1] - 1), 4)
        else:
            row["change_365d"] = None
            row["note"] = "pending: fewer than 365 days since the election"
        out.append(row)
    return out


def report(daily: pd.DataFrame, min_drawdown: float = 0.5, min_days: int = 180) -> dict:
    close = daily["close"]
    rows = cycles(close, min_drawdown)
    return {
        "as_of": _day(close.index[-1]), "first_day": _day(close.index[0]),
        "cycles": rows, "sma200_runs": sma200_runs(close, min_days), "midterms": midterms(close, rows),
        "notes": ["descriptive history from daily closes, not a forecast",
                  "each list is one row per past episode: its length is the sample size",
                  "a cycle's peak is only known once the drawdown has happened; 'complete' needs a new high"],
    }
