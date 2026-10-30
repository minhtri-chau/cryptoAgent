"""Historical analogues, version analog-v1: when did this market look like today, and what
happened next?

Definition (changing any part makes a new version):
- state of day t: FEATURES, all computed from bars up to t (indicators are causal)
- searching as of day D: candidate days s whose whole forward window ended by D
  (position s + horizon <= position D), with every feature present; at least MIN_CANDIDATES
- features are z-scored with the candidates' mean and standard deviation; distance is
  Euclidean with equal weights
- neighbours: the k closest candidates, taken greedily so no two are within `horizon`
  bars of each other; each neighbour is therefore a separate episode, and the neighbour
  count is the effective sample size
- outcomes: return from the close of s to the close `horizon` bars later, and the worst
  low / best high in between (MAE / MFE); the same returns over every candidate are the
  unconditional base rate
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cryptoagent.quant import indicators as ind
from cryptoagent.quant import regime

VERSION = "analog-v1"
FEATURES = ("roc7", "roc30", "roc90", "ema200_dev", "sma200_change20", "log_vol30", "rsi14", "drawdown_ath")
MIN_CANDIDATES = 200
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 0


class AnalogError(ValueError):
    pass


def states(df: pd.DataFrame) -> pd.DataFrame:
    """The analog-v1 state of every day, plus the regime-v1 trend label for context."""
    i = ind.compute_all(df)
    out = i[[f for f in FEATURES if f != "log_vol30"]].copy()
    out["log_vol30"] = np.log(i["vol30"])
    out["trend"] = regime.classify(df)["trend"]
    return out[list(FEATURES) + ["trend"]]


def _ci90(values: np.ndarray) -> list[float] | None:
    if len(values) < 2:
        return None
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    means = rng.choice(values, size=(BOOTSTRAP_SAMPLES, len(values)), replace=True).mean(axis=1)
    return [round(float(np.quantile(means, 0.05)), 6), round(float(np.quantile(means, 0.95)), 6)]


def _distribution(returns: np.ndarray) -> dict:
    if not len(returns):
        return {"n": 0}
    q = np.quantile(returns, [0.10, 0.25, 0.50, 0.75, 0.90])
    up = (returns > 0).astype(float)
    return {"n": int(len(returns)), "mean": round(float(returns.mean()), 6), "mean_ci90": _ci90(returns),
            "share_up": round(float(up.mean()), 4), "share_up_ci90": _ci90(up),
            "quantiles": dict(zip(("p10", "p25", "p50", "p75", "p90"), (round(float(v), 6) for v in q)))}


def search(df: pd.DataFrame, horizon: int = 30, k: int = 20, precomputed: pd.DataFrame | None = None) -> dict:
    """Analogues of the last day of `df`, using only `df`.

    `precomputed` may hold states() of a longer frame that starts like `df`: valid because
    every state is causal, and it avoids recomputing indicators in walk-forward evaluation.
    """
    if horizon < 1 or k < 1:
        raise AnalogError("horizon and k must be positive")
    st = (states(df) if precomputed is None else precomputed).iloc[:len(df)]
    today = st.iloc[-1]
    if today[list(FEATURES)].isna().any():
        raise AnalogError("today's state is incomplete (needs ~200 days of history)")
    last = len(df) - 1
    features = st[list(FEATURES)].to_numpy(dtype=float)
    eligible = np.flatnonzero(~np.isnan(features).any(axis=1) & (np.arange(len(st)) + horizon <= last))
    if len(eligible) < MIN_CANDIDATES:
        raise AnalogError(f"only {len(eligible)} candidate days with a completed {horizon}-day outcome "
                          f"(need {MIN_CANDIDATES})")
    cand = features[eligible]
    mu, sd = cand.mean(axis=0), cand.std(axis=0)
    sd[sd == 0] = 1.0
    target = today[list(FEATURES)].to_numpy(dtype=float)
    dist = np.sqrt((((cand - mu) / sd - (target - mu) / sd) ** 2).sum(axis=1))
    chosen: list[int] = []
    for idx in np.argsort(dist, kind="stable"):
        pos = int(eligible[idx])
        if all(abs(pos - c) >= horizon for c in chosen):
            chosen.append(pos)
            if len(chosen) == k:
                break
    close, high, low = (df[c].to_numpy(dtype=float) for c in ("close", "high", "low"))
    fwd_all = close[eligible + horizon] / close[eligible] - 1
    rows = []
    for pos in chosen:
        window = slice(pos + 1, pos + horizon + 1)
        rows.append({
            "day": df.index[pos].date().isoformat(),
            "distance": round(float(dist[np.searchsorted(eligible, pos)]), 4),
            "trend": st["trend"].iloc[pos] if isinstance(st["trend"].iloc[pos], str) else None,
            "forward_return": round(float(close[pos + horizon] / close[pos] - 1), 6),
            "mae": round(float(low[window].min() / close[pos] - 1), 6),
            "mfe": round(float(high[window].max() / close[pos] - 1), 6),
            "state": {f: round(float(st[f].iloc[pos]), 4) for f in FEATURES},
        })
    neighbour_returns = np.array([r["forward_return"] for r in rows])
    today_trend = today["trend"] if isinstance(today["trend"], str) else None
    return {
        "version": VERSION, "as_of": df.index[-1].date().isoformat(), "horizon_days": horizon,
        "today": {"state": {f: round(float(today[f]), 4) for f in FEATURES}, "trend": today_trend},
        "candidates": int(len(eligible)),
        "effective_n": len(rows),
        "analogues": _distribution(neighbour_returns) | {
            "mean_mae": round(float(np.mean([r["mae"] for r in rows])), 6) if rows else None,
            "mean_mfe": round(float(np.mean([r["mfe"] for r in rows])), 6) if rows else None,
            "same_trend_as_today": sum(1 for r in rows if today_trend is not None and r["trend"] == today_trend)},
        "unconditional": _distribution(fwd_all) | {
            "note": "every candidate day; overlapping windows, so n overstates independent evidence"},
        "neighbours": rows,
    }


def evaluate(df: pd.DataFrame, horizon: int, k: int, start: pd.Timestamp | None) -> dict:
    """Walk-forward: on dates `horizon` bars apart from `start`, P(up) from analogues vs the base
    rate, each using only data up to that date, scored with Brier against the outcome."""
    st = states(df)
    first = 0 if start is None else int(df.index.searchsorted(start))
    close = df["close"].to_numpy(dtype=float)
    rows, skipped = [], 0
    pos = first
    while pos + horizon <= len(df) - 1:
        try:
            r = search(df.iloc[:pos + 1], horizon, k, precomputed=st)
        except AnalogError:
            skipped += 1
            pos += horizon
            continue
        up = float(close[pos + horizon] > close[pos])
        p_an, p_base = r["analogues"]["share_up"], r["unconditional"]["share_up"]
        rows.append({"day": df.index[pos].date().isoformat(), "p_analog": p_an, "p_base": p_base, "up": up,
                     "brier_analog": (p_an - up) ** 2, "brier_base": (p_base - up) ** 2})
        pos += horizon
    out = {"version": VERSION, "horizon_days": horizon, "k": k, "evaluations": len(rows),
           "skipped_insufficient_history": skipped}
    if rows:
        frame = pd.DataFrame(rows)
        b_an, b_base = frame["brier_analog"].mean(), frame["brier_base"].mean()
        out.update({
            "first_day": rows[0]["day"], "last_day": rows[-1]["day"],
            "brier_analog": round(float(b_an), 6), "brier_base": round(float(b_base), 6),
            "brier_skill_vs_base": round(float(1 - b_an / b_base), 6) if b_base > 0 else None,
            "observed_up": round(float(frame["up"].mean()), 4),
            "mean_p_analog": round(float(frame["p_analog"].mean()), 4),
            "mean_p_base": round(float(frame["p_base"].mean()), 4),
            "brier_diff_ci90": _ci90((frame["brier_base"] - frame["brier_analog"]).to_numpy()),
            "dates": rows,
        })
    return out
