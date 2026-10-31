"""The CLI is the only interface skills use, so its outputs must be JSON and its refusals
must exit with status 2 instead of half-working."""

import json

from cryptoagent import db
from cryptoagent.cli import main


def test_refuses_to_run_with_pending_migrations(tmp_path, capsys):
    db.connect(tmp_path / "fresh.db").close()
    assert main(["--db", str(tmp_path / "fresh.db"), "markets"]) == 2
    assert "init-db" in capsys.readouterr().err


def test_init_then_read_commands_print_json(tmp_path, capsys):
    path = str(tmp_path / "t.db")
    assert main(["--db", path, "init-db"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] == ["001_market_data.sql", "002_predictions.sql", "003_trials.sql",
                                                                 "004_hourly_and_recommendations.sql", "005_research.sql", "006_fundamentals.sql"]
    assert main(["--db", path, "markets"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert main(["--db", path, "quality"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"] == {}


def test_unknown_market_exits_2(tmp_path, capsys):
    path = str(tmp_path / "t.db")
    main(["--db", path, "init-db"])
    assert main(["--db", path, "candles", "coinbase:NOPE/USD"]) == 2
    assert "unknown market" in capsys.readouterr().err


def test_bad_prediction_input_exits_2(tmp_path, capsys):
    path = str(tmp_path / "t.db")
    main(["--db", path, "init-db"])
    args = ["--db", path, "predict", "add", "--market", "coinbase:BTC/USD", "--horizon", "7", "--prob-up", "0.6",
            "--by", "claude", "--rationale", "x"]
    assert main(args) == 2  # unknown market: no data yet
    assert "unknown market" in capsys.readouterr().err
