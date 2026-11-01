"""The registry exists so outside analysts shape which questions get asked, never the answers:
- one creator repeating an idea is not confirmation;
- the tester never learns who proposed an idea, and status changes rest on that idea's own trials;
- hindsight can't pass as foresight: forward evidence must postdate registration;
- every record is append-only, and only listed status changes are possible;
- a source's conditional scenarios, judged by criteria we wrote, are never scored as theirs."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from cryptoagent import calls, hypotheses as hy, predictions, store
from cryptoagent.cli import main

URL = "https://www.youtube.com/watch?v=abc&t=60s"


def register(conn, slug="weekly-pivot-failure", kind="testable"):
    return hy.add_hypothesis(conn, slug=slug, kind=kind, subject="btc",
                             statement="Two consecutive weekly closes below the last confirmed weekly pivot high, "
                                       "after a close above it, precede weaker 4-12 week returns",
                             conditions="BTC, any regime", required_data="BTC weekly candles",
                             methodology="Event study against all-days drift", created_by="agent")["hypothesis_id"]


def mention(conn, hid, creator="Jane Analyst", url=URL, published="2026-10-30"):
    return hy.add_mention(conn, hypothesis_id=hid, creator=creator, url=url, published_on=published,
                          date_basis="stated in the video", recorded_by="agent")


def trial(conn, family, period):
    with conn:
        return conn.execute(
            "INSERT INTO trials (created_by, family, hypothesis, spec, spec_hash, market, period, data_through, costs,"
            " trades, results) VALUES ('claude', ?, 'h', '{}', ?, 'bitstamp:BTC/USD', ?, '2026-10-31', '{}', 40, '{}')",
            (family, f"{family}-{period}", period)).lastrowid


def test_repeats_by_one_creator_are_mentions_not_independent_support(conn):
    hid = register(conn)
    mention(conn, hid, url=URL)
    again = mention(conn, hid, url="https://www.youtube.com/watch?v=def")
    assert again["mentions"] == 2 and again["independent_sources"] == 1 and again["repeat_by_same_creator"]
    other = mention(conn, hid, creator="Another Person", url="https://example.com/post")
    assert other["independent_sources"] == 2 and not other["repeat_by_same_creator"]
    with pytest.raises(sqlite3.IntegrityError):  # the same video twice is not even a second mention
        mention(conn, hid, url=URL)


def test_the_tester_never_sees_the_source(conn):
    hid = register(conn)
    mention(conn, hid, creator="Jane Analyst", published="2026-10-30")
    b = hy.brief(conn, hid)
    text = str(b).lower()
    assert "analyst" not in text and "youtube" not in text and "2026-10-30" not in text
    assert b["family"] == f"hyp:{hid}"
    with pytest.raises(hy.RegistryError, match="names its source"):  # nor can an alternative name it
        hy.add_alternative(conn, hid, "Analyst momentum is already priced", "drift")
    bad = hy.add_hypothesis(conn, slug="analyst-rule", kind="testable", subject="BTC",
                            statement="Analyst's weekly rule", conditions="any", required_data="candles",
                            methodology="event study", created_by="agent")["hypothesis_id"]
    with pytest.raises(hy.RegistryError, match="names its source"):  # a definition naming its source can't go blind
        mention(conn, bad, creator="Jane Analyst")


def test_a_test_verdict_must_rest_on_this_hypothesis_own_trials(conn):
    hid = register(conn)
    with pytest.raises(hy.RegistryError, match="needs the train trial ids"):
        hy.set_status(conn, hid, "train_supported", "agent")
    elsewhere = trial(conn, "learn:someone", "train")
    with pytest.raises(hy.RegistryError, match="family hyp:"):
        hy.set_status(conn, hid, "train_supported", "agent", trial_ids=[elsewhere])
    validation_run = trial(conn, f"hyp:{hid}", "validation")
    with pytest.raises(hy.RegistryError, match="needs train"):
        hy.set_status(conn, hid, "train_supported", "agent", trial_ids=[validation_run])
    own = trial(conn, f"hyp:{hid}", "train")
    assert hy.set_status(conn, hid, "train_supported", "agent", trial_ids=[own])["status"] == "train_supported"
    assert [t["id"] for t in hy.show(conn, hid)["trials"]] == [validation_run, own]


def test_only_listed_status_changes_and_nothing_is_rewritten(conn):
    hid = register(conn)
    with pytest.raises(hy.RegistryError, match="cannot go from registered to validated"):
        hy.set_status(conn, hid, "validated", "agent", trial_ids=[trial(conn, f"hyp:{hid}", "validation")])
    with pytest.raises(sqlite3.IntegrityError):  # the database refuses it even without the module
        with conn:
            conn.execute("INSERT INTO hypothesis_events (hypothesis_id, status, decided_by) VALUES (?, 'validated',"
                         " 'agent')", (hid,))
    for table in ("hypotheses", "hypothesis_events"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with conn:
                conn.execute(f"DELETE FROM {table}")
    with pytest.raises(hy.RegistryError, match="needs a note"):
        hy.set_status(conn, hid, "retired", "agent")


def test_observations_need_checked_evidence_and_commands_need_their_data_date(conn):
    hid = register(conn, slug="lows-after-elections", kind="observation")
    with pytest.raises(hy.RegistryError, match="retrospective evidence"):
        hy.set_status(conn, hid, "consistent", "agent")
    with pytest.raises(hy.RegistryError, match="data-through"):
        hy.add_evidence(conn, hid, direction="supports", basis="retrospective", summary="3 of 3",
                        cite="command:cycles BTC", created_by="agent")
    hy.add_evidence(conn, hid, direction="supports", basis="retrospective", summary="3 of 3",
                    cite="command:cycles BTC", created_by="agent", data_through="2026-10-31")
    assert hy.set_status(conn, hid, "consistent", "agent")["status"] == "consistent"
    with pytest.raises(hy.RegistryError, match="train_supported"):
        hy.set_status(conn, hid, "train_supported", "agent", trial_ids=[trial(conn, f"hyp:{hid}", "train")])


@pytest.fixture
def btc(conn):
    source_id = store.get_or_create_source(conn, "coinbase", "exchange")
    run_id = store.start_run(conn, source_id, {})
    market_id = store.get_or_create_market(conn, source_id, "BTC", "USD", "BTC/USD", "BTC-USD")
    days = pd.date_range("2026-01-01", periods=30, freq="D")
    with conn:
        conn.executemany(
            "INSERT INTO candles (market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id)"
            " VALUES (?, '1d', ?, ?, ?, ?, ?, 1, ?)",
            [(market_id, d.strftime("%Y-%m-%dT00:00:00Z"), c, c, c, c, run_id)
             for d, c in zip(days, np.linspace(100, 130, 30))])
    return conn


def test_forward_evidence_must_be_recorded_after_registration(btc):
    early = predictions.record(btc, "coinbase:BTC/USD", 30, 0.6, "model", "base_rate_v1")
    with btc:  # registered after the prediction was made (rows are append-only, so set the time on insert)
        late = btc.execute("INSERT INTO hypotheses (slug, kind, subject, statement, conditions, required_data,"
                           " methodology, created_by, created_at) VALUES ('late', 'testable', 'BTC', 's', 'c', 'd', 'm',"
                           " 'agent', '2999-01-01T00:00:00Z')").lastrowid
    with pytest.raises(hy.RegistryError, match="retrospective, not forward"):
        hy.add_evidence(btc, late, direction="supports", basis="forward", summary="s", cite=f"prediction:{early}",
                        created_by="agent")
    hid = register(btc)
    with pytest.raises(hy.RegistryError, match="must cite a prediction"):
        hy.add_evidence(btc, hid, direction="supports", basis="forward", summary="s", cite="url:https://x.org",
                        created_by="agent")
    later = predictions.record(btc, "coinbase:BTC/USD", 7, 0.55, "model", "base_rate_v1")
    assert hy.add_evidence(btc, hid, direction="neutral", basis="forward", summary="s", cite=f"prediction:{later}",
                           created_by="agent")["evidence_id"]


def test_scenarios_are_monitored_but_never_scored_as_the_creators(conn):
    s = hy.add_scenario(conn, creator="Jane Analyst", url=URL, published_on="2026-10-30", date_basis="stated",
                        description="a bounce at the support band, then a final drop after the elections",
                        resolution="BTC closes below 58,526 before 2027-01-31", deadline="2027-01-31",
                        recorded_by="agent")
    assert hy.list_scenarios(conn)[0]["scored_as_creator"] is False
    assert calls.score(conn, "Jane Analyst") == []  # the creator's scorecard only holds exact calls
    hy.resolve_scenario(conn, s["scenario_id"], "not_met", "no close below", "command:candles", "agent")
    with pytest.raises(sqlite3.IntegrityError):  # resolved once, never revised
        hy.resolve_scenario(conn, s["scenario_id"], "met", "changed my mind", "command:candles", "agent")


def test_concepts_are_kept_without_a_path_to_validation(conn):
    old = hy.add_concept(conn, slug="narrative-follows-price", name="Narrative follows price",
                         summary="Explanations are attached after the move", usage="Discount news-driven theses",
                         limits="Not a causal law", created_by="agent")["concept_id"]
    hy.add_mention(conn, concept_id=old, creator="Jane Analyst", url=URL, published_on="2026-10-30",
                   date_basis="stated", recorded_by="agent")
    new = hy.add_concept(conn, slug="narrative-follows-price-v2", name="Narrative follows price",
                         summary="Explanations are usually attached after the move", usage="Discount news theses",
                         limits="Not a causal law", created_by="agent", supersedes_id=old)["concept_id"]
    listed = hy.list_concepts(conn)
    assert [c["id"] for c in listed] == [new] and "not a validated rule" in listed[0]["note"]
    assert "status" not in listed[0]
    assert hy.similar(conn, "news narrative follows the price")[0]["type"] == "concept"


def test_cli_round_trip_and_errors_exit_2(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    assert main(["--db", db, "init-db"]) == 0
    args = ["--db", db, "hypothesis", "add", "--slug", "band-touch", "--kind", "testable", "--subject", "BTC",
            "--text", "First touch of the 20-week SMA after a failed breakout precedes a lower low",
            "--conditions", "after a failed weekly breakout", "--data", "weekly candles",
            "--method", "event study", "--by", "agent"]
    assert main(args) == 0
    assert main(["--db", db, "hypothesis", "status", "1", "--to", "validated", "--by", "agent"]) == 2
    assert main(["--db", db, "hypothesis", "brief", "1"]) == 0
    assert '"family": "hyp:1"' in capsys.readouterr().out
