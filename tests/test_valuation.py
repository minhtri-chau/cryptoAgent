"""Scenario prices are what the user weighs a token against, so the arithmetic must be exact, the
forward and reverse calculations must agree, dilution must count, and no assumption may be filled
in silently: an unstated multiple or growth rate would be a hidden forecast."""

import json

import numpy as np
import pandas as pd
import pytest

from cryptoagent import valuation
from cryptoagent.cli import main
from cryptoagent.valuation import ValuationError

BASE = dict(price=100.0, supply=1_000_000.0, annual_metric=5_000_000.0)  # market cap 100M, revenue 5M: 20x today


def test_multiple_arithmetic_with_growth_and_dilution():
    r = valuation.multiple(**BASE, growth=0.25, multiple=20, years=2, dilution=0.10)
    future_metric = 5_000_000 * 1.25 ** 2          # 7.8125M
    future_supply = 1_000_000 * 1.10 ** 2           # 1.21M
    assert r["implied_price"] == pytest.approx(future_metric * 20 / future_supply)  # ~129.13
    assert r["today_multiple"] == 20.0
    undiluted = valuation.multiple(**BASE, growth=0.25, multiple=20, years=2, dilution=0.0)
    assert undiluted["implied_price"] > r["implied_price"]  # dilution lowers the price per token


def test_reverse_is_consistent_with_forward():
    rev = valuation.reverse(**BASE, multiple=15, years=3, dilution=0.05)
    fwd = valuation.multiple(**BASE, growth=rev["required_growth_per_year"], multiple=15, years=3, dilution=0.05)
    assert fwd["implied_price"] == pytest.approx(100.0, rel=1e-5)  # the required growth reproduces today's price
    assert "only if revenue grows" in rev["reading"]


def test_relative_to_a_peer():
    r = valuation.relative(price=100.0, supply=1_000_000.0, peer_market_cap=1e9, fraction=0.25, peer="ETH")
    assert r["implied_price"] == pytest.approx(250.0) and r["today_fraction_of_peer"] == pytest.approx(0.1)


@pytest.mark.parametrize("missing", ["growth", "multiple", "years", "dilution"])
def test_every_assumption_must_be_stated(missing):
    kwargs = dict(growth=0.2, multiple=20, years=2, dilution=0.0) | {missing: None}
    with pytest.raises(ValuationError, match=f"missing: {missing}"):
        valuation.multiple(**BASE, **kwargs)


def test_history_distribution_reports_independent_sample():
    close = 100 * np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.02, 1000)))
    df = pd.DataFrame({"close": close}, index=pd.date_range("2020-01-01", periods=1000, freq="D"))
    h = valuation.history(df, 365)
    assert h["windows"] == 635 and h["approx_independent"] == 1
    assert h["quantiles"]["p10"] <= h["quantiles"]["p50"] <= h["quantiles"]["p90"]


def test_cli_runs_offline_with_explicit_inputs(tmp_path, capsys):
    path = str(tmp_path / "t.db")
    main(["--db", path, "init-db"])
    capsys.readouterr()
    assert main(["--db", path, "valuation", "AAVE", "--method", "multiple", "--price", "100", "--supply", "1000000",
                 "--metric-value", "5000000", "--multiple", "20", "--growth", "0", "--years", "1", "--dilution", "0",
                 "--case", "base"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["implied_price"] == pytest.approx(100.0) and out["case"] == "base" and "not a forecast" in out["label"]
    assert main(["--db", path, "valuation", "AAVE", "--method", "multiple", "--price", "100", "--supply", "1000000",
                 "--metric-value", "5000000", "--multiple", "20", "--years", "1", "--dilution", "0"]) == 2
    assert "missing: growth" in capsys.readouterr().err
