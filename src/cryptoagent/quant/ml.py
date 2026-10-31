"""Machine-learning baselines, judged by one question: do they beat the base rate out of sample?

Definition (changing any part makes a new version):
- features: the analog-v1 state of each day (causal) plus the regime-v1 trend label, one-hot
- target: close `horizon` bars later above today's close
- models: ml-logit-v1 (standardised logistic regression) and ml-gbm-v1 (histogram gradient
  boosting, depth 3, 100 iterations, learning rate 0.05); fixed random state
- walk-forward: on dates `horizon` bars apart, fit only on rows whose whole forward window ended
  by that date (the purge), with at least MIN_TRAIN rows and both outcomes present; compare the
  model's P(up) with the base rate of the same training rows by Brier score
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from cryptoagent.quant import analogs

MODELS = {"logit": "ml-logit-v1", "gbm": "ml-gbm-v1"}
MIN_TRAIN = 500
TRENDS = ("uptrend", "downtrend", "transition")


class MLError(ValueError):
    pass


def make_model(kind: str):
    if kind == "logit":
        return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=1000))
    if kind == "gbm":
        return HistGradientBoostingClassifier(max_depth=3, max_iter=100, learning_rate=0.05, random_state=0)
    raise MLError(f"unknown model {kind!r}; use {', '.join(MODELS)}")


def dataset(df: pd.DataFrame, horizon: int, states: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.Series]:
    """Features for every day and the outcome `horizon` bars later (NaN where that is beyond `df`)."""
    st = analogs.states(df) if states is None else states.iloc[:len(df)]
    X = st[list(analogs.FEATURES)].copy()
    for trend in TRENDS:
        X[f"trend_{trend}"] = (st["trend"] == trend).astype(float)
    close = df["close"].to_numpy(dtype=float)
    future = np.full(len(df), np.nan)
    future[:len(df) - horizon] = close[horizon:]
    y = pd.Series(np.where(np.isnan(future), np.nan, (future > close).astype(float)), index=df.index)
    return X, y


def evaluate(df: pd.DataFrame, kind: str, horizon: int, start: pd.Timestamp | None) -> dict:
    make_model(kind)  # validates the kind before any work
    X, y = dataset(df, horizon)
    complete = X.notna().all(axis=1).to_numpy()
    labels = y.to_numpy()
    first = 0 if start is None else int(df.index.searchsorted(start))
    rows, skipped, latest_training_row = [], 0, []
    pos = first
    while pos + horizon <= len(df) - 1:
        train = np.flatnonzero(complete & (np.arange(len(df)) + horizon <= pos))  # outcome known by `pos`
        if not complete[pos] or len(train) < MIN_TRAIN or len(np.unique(labels[train])) < 2:
            skipped += 1
            pos += horizon
            continue
        model = make_model(kind).fit(X.iloc[train], labels[train].astype(int))
        p_model = float(model.predict_proba(X.iloc[[pos]])[0, 1])
        p_base = float(labels[train].mean())
        up = float(labels[pos])
        rows.append({"day": df.index[pos].date().isoformat(), "p_model": round(p_model, 6), "p_base": round(p_base, 6),
                     "up": up, "brier_model": (p_model - up) ** 2, "brier_base": (p_base - up) ** 2,
                     "training_rows": int(len(train))})
        latest_training_row.append((pos, int(train.max())))
        pos += horizon
    out = {"version": MODELS[kind], "horizon_days": horizon, "evaluations": len(rows),
           "skipped_insufficient_history": skipped}
    if rows:
        frame = pd.DataFrame(rows)
        b_model, b_base = frame["brier_model"].mean(), frame["brier_base"].mean()
        out.update({
            "first_day": rows[0]["day"], "last_day": rows[-1]["day"],
            "brier_model": round(float(b_model), 6), "brier_base": round(float(b_base), 6),
            "brier_skill_vs_base": round(float(1 - b_model / b_base), 6) if b_base > 0 else None,
            "brier_diff_ci90": analogs._ci90((frame["brier_base"] - frame["brier_model"]).to_numpy()),
            "observed_up": round(float(frame["up"].mean()), 4),
            "mean_p_model": round(float(frame["p_model"].mean()), 4),
            "mean_p_base": round(float(frame["p_base"].mean()), 4),
            "dates": rows,
        })
    out["_purge_check"] = latest_training_row  # (evaluation position, latest training position) for tests
    return out
