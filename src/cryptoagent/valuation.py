"""Scenario valuation: what price do stated assumptions imply? Deterministic arithmetic only.

Claude proposes the assumptions (and cites why); this module computes their consequences, so a
scenario is reproducible and its inputs are explicit. Nothing has a default: a scenario without a
stated multiple, growth rate, horizon or dilution rate is refused rather than silently filled in.
Results are conditional ("if these assumptions hold, then…"), never forecasts.

Methods:
- multiple: future annual metric (fees or revenue) x multiple = implied market cap; divided by the
  future circulating supply (today's supply grown by the dilution rate) = implied price
- reverse: the metric growth rate today's price already assumes, at a given multiple and horizon
  (forward with that growth reproduces today's price exactly)
- relative: price if the market cap reaches a stated fraction of a peer's market cap
- history: how the coin's own returns over the horizon were distributed in the past (overlapping
  windows; the independent sample size is reported)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class ValuationError(ValueError):
    pass


def _require(**values) -> None:
    missing = [k for k, v in values.items() if v is None]
    if missing:
        raise ValuationError(f"state every assumption explicitly; missing: {', '.join(missing)}")


def _future_supply(supply: float, dilution: float, years: float) -> float:
    return supply * (1 + dilution) ** years


def multiple(*, price: float, supply: float, annual_metric: float, growth: float | None, multiple: float | None,
             years: float | None, dilution: float | None, metric_name: str = "revenue") -> dict:
    _require(growth=growth, multiple=multiple, years=years, dilution=dilution)
    if annual_metric is None or annual_metric <= 0 or supply <= 0 or price <= 0:
        raise ValuationError("needs a positive price, supply and annual metric")
    future_metric = annual_metric * (1 + growth) ** years
    future_supply = _future_supply(supply, dilution, years)
    implied_mcap = future_metric * multiple
    implied_price = implied_mcap / future_supply
    return {
        "method": "multiple", "metric": metric_name,
        "assumptions": {"growth_per_year": growth, "multiple": multiple, "years": years, "dilution_per_year": dilution},
        "inputs": {"price": price, "circulating_supply": supply, f"annual_{metric_name}": annual_metric},
        "today_multiple": round(price * supply / annual_metric, 4),
        "future_annual_metric": round(future_metric, 2), "future_supply": round(future_supply, 2),
        "implied_market_cap": round(implied_mcap, 2), "implied_price": round(implied_price, 8),
        "change_from_today": round(implied_price / price - 1, 6),
        "formula": f"{metric_name} x (1 + growth)^years x multiple / (supply x (1 + dilution)^years)",
    }


def reverse(*, price: float, supply: float, annual_metric: float, multiple: float | None, years: float | None,
            dilution: float | None, metric_name: str = "revenue") -> dict:
    _require(multiple=multiple, years=years, dilution=dilution)
    if annual_metric is None or annual_metric <= 0 or years <= 0:
        raise ValuationError("needs a positive annual metric and horizon")
    # Today's price held at the horizon needs: metric_T x multiple = price x future supply.
    required_metric = price * _future_supply(supply, dilution, years) / multiple
    required_growth = (required_metric / annual_metric) ** (1 / years) - 1
    return {
        "method": "reverse", "metric": metric_name,
        "assumptions": {"multiple": multiple, "years": years, "dilution_per_year": dilution},
        "inputs": {"price": price, "circulating_supply": supply, f"annual_{metric_name}": annual_metric},
        "today_multiple": round(price * supply / annual_metric, 4),
        "required_annual_metric_at_horizon": round(required_metric, 2),
        "required_growth_per_year": round(required_growth, 6),
        "reading": f"today's price is justified at a {multiple}x {metric_name} multiple only if {metric_name} grows "
                   f"{required_growth:.1%} a year for {years:g} years (with {dilution:.1%} yearly dilution)",
    }


def relative(*, price: float, supply: float, peer_market_cap: float, fraction: float | None, peer: str,
             dilution: float | None = 0.0, years: float | None = 0.0) -> dict:
    _require(fraction=fraction)
    future_supply = _future_supply(supply, dilution or 0.0, years or 0.0)
    implied_price = fraction * peer_market_cap / future_supply
    return {
        "method": "relative", "peer": peer,
        "assumptions": {"fraction_of_peer_market_cap": fraction, "years": years, "dilution_per_year": dilution},
        "inputs": {"price": price, "circulating_supply": supply, "peer_market_cap": peer_market_cap},
        "today_fraction_of_peer": round(price * supply / peer_market_cap, 6),
        "implied_price": round(implied_price, 8), "change_from_today": round(implied_price / price - 1, 6),
    }


def history(df: pd.DataFrame, horizon_days: int) -> dict:
    """Distribution of the coin's past returns over the horizon (all windows ending inside `df`)."""
    close = df["close"].to_numpy(dtype=float)
    if len(close) <= horizon_days + 30:
        raise ValuationError(f"not enough history for a {horizon_days}-day distribution")
    returns = close[horizon_days:] / close[:-horizon_days] - 1
    q = np.quantile(returns, [0.10, 0.25, 0.50, 0.75, 0.90])
    return {
        "method": "history", "horizon_days": horizon_days, "windows": int(len(returns)),
        "approx_independent": int(len(returns) // horizon_days),
        "from": df.index[0].date().isoformat(), "to": df.index[-1].date().isoformat(),
        "quantiles": dict(zip(("p10", "p25", "p50", "p75", "p90"), (round(float(v), 6) for v in q))),
        "share_positive": round(float((returns > 0).mean()), 4),
        "worst": round(float(returns.min()), 6), "best": round(float(returns.max()), 6),
        "note": "this coin's own past; overlapping windows; it includes regimes that may not repeat",
    }
