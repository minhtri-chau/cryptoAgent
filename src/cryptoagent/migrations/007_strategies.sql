-- 007: strategy lifecycle (promotion-v1) and the paper forward test. Everything is append-only;
-- a strategy's stage is its latest event, and only allowed transitions can be recorded.

CREATE TABLE strategies (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE CHECK (name <> ''),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE strategy_versions (
    id           INTEGER PRIMARY KEY,
    strategy_id  INTEGER NOT NULL REFERENCES strategies(id),
    version      INTEGER NOT NULL CHECK (version >= 1),
    spec         TEXT NOT NULL,                 -- canonical backtest spec (JSON)
    spec_hash    TEXT NOT NULL,
    rationale    TEXT NOT NULL CHECK (rationale <> ''),
    created_by   TEXT NOT NULL CHECK (created_by IN ('agent', 'user')),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (strategy_id, version)
);

-- Allowed stage transitions ('' = no event yet). User-only events are listed in the trigger below.
CREATE TABLE strategy_transitions (from_event TEXT NOT NULL, to_event TEXT NOT NULL, PRIMARY KEY (from_event, to_event));
INSERT INTO strategy_transitions VALUES
    ('', 'proposed'),
    ('proposed', 'validation_passed'), ('proposed', 'validation_failed'),
    ('validation_passed', 'test_authorized'), ('validation_passed', 'rejected'),
    ('test_authorized', 'test_passed'), ('test_authorized', 'test_failed'),
    ('test_passed', 'paper'),
    ('paper', 'paper_review'), ('paper', 'rejected'),
    ('paper_review', 'approved'), ('paper_review', 'rejected'),
    ('approved', 'retired');
CREATE TRIGGER strategy_transitions_frozen_insert BEFORE INSERT ON strategy_transitions
BEGIN SELECT RAISE(ABORT, 'allowed transitions change only through a migration'); END;
CREATE TRIGGER strategy_transitions_frozen_update BEFORE UPDATE ON strategy_transitions
BEGIN SELECT RAISE(ABORT, 'allowed transitions change only through a migration'); END;
CREATE TRIGGER strategy_transitions_frozen_delete BEFORE DELETE ON strategy_transitions
BEGIN SELECT RAISE(ABORT, 'allowed transitions change only through a migration'); END;

CREATE TABLE strategy_events (
    id                  INTEGER PRIMARY KEY,
    strategy_version_id INTEGER NOT NULL REFERENCES strategy_versions(id),
    event               TEXT NOT NULL,
    decided_by          TEXT NOT NULL CHECK (decided_by IN ('system', 'agent', 'user')),
    note                TEXT,
    evidence            TEXT,                   -- JSON: trial ids, gate results
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX idx_strategy_events_version ON strategy_events(strategy_version_id, id);

CREATE TRIGGER strategy_events_transition BEFORE INSERT ON strategy_events
WHEN NOT EXISTS (
    SELECT 1 FROM strategy_transitions
    WHERE to_event = NEW.event
      AND from_event = coalesce((SELECT event FROM strategy_events WHERE strategy_version_id = NEW.strategy_version_id
                                 ORDER BY id DESC LIMIT 1), ''))
BEGIN
    SELECT RAISE(ABORT, 'this stage change is not allowed from the strategy''s current stage');
END;

CREATE TRIGGER strategy_events_user_only BEFORE INSERT ON strategy_events
WHEN NEW.event IN ('test_authorized', 'approved', 'rejected', 'retired') AND NEW.decided_by <> 'user'
BEGIN
    SELECT RAISE(ABORT, 'only the user can authorise the test, approve, reject or retire a strategy');
END;

-- Paper forward test: a signal on the latest candle records a paper trade, resolved later by replay.
CREATE TABLE paper_trades (
    id                  INTEGER PRIMARY KEY,
    strategy_version_id INTEGER NOT NULL REFERENCES strategy_versions(id),
    market_id           INTEGER NOT NULL REFERENCES markets(id),
    as_of               TEXT NOT NULL,
    as_of_close         REAL NOT NULL CHECK (as_of_close > 0),
    atr                 REAL CHECK (atr IS NULL OR atr > 0),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (strategy_version_id, market_id, as_of)
);

CREATE TRIGGER paper_trades_bind_latest_candle BEFORE INSERT ON paper_trades
WHEN NEW.as_of IS NOT (SELECT substr(max(ts), 1, 10) FROM candles WHERE market_id = NEW.market_id AND timeframe = '1d')
  OR NEW.as_of_close IS NOT (SELECT close FROM candles WHERE market_id = NEW.market_id AND timeframe = '1d'
                             AND ts = NEW.as_of || 'T00:00:00Z')
BEGIN
    SELECT RAISE(ABORT, 'a paper trade must use the latest stored candle and its close');
END;

CREATE TABLE paper_outcomes (
    paper_trade_id INTEGER PRIMARY KEY REFERENCES paper_trades(id),
    resolved_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    status         TEXT NOT NULL CHECK (status IN ('resolved', 'unresolvable')),
    trade_return   REAL,
    exit_reason    TEXT,
    entry_price    REAL,
    exit_price     REAL,
    exit_day       TEXT,
    CHECK ((status = 'resolved') = (trade_return IS NOT NULL))
);

CREATE TRIGGER strategy_versions_no_update BEFORE UPDATE ON strategy_versions
BEGIN SELECT RAISE(ABORT, 'strategy versions are append-only'); END;
CREATE TRIGGER strategy_versions_no_delete BEFORE DELETE ON strategy_versions
BEGIN SELECT RAISE(ABORT, 'strategy versions are append-only'); END;
CREATE TRIGGER strategy_events_no_update BEFORE UPDATE ON strategy_events
BEGIN SELECT RAISE(ABORT, 'strategy events are append-only'); END;
CREATE TRIGGER strategy_events_no_delete BEFORE DELETE ON strategy_events
BEGIN SELECT RAISE(ABORT, 'strategy events are append-only'); END;
CREATE TRIGGER paper_trades_no_update BEFORE UPDATE ON paper_trades
BEGIN SELECT RAISE(ABORT, 'paper trades are append-only'); END;
CREATE TRIGGER paper_trades_no_delete BEFORE DELETE ON paper_trades
BEGIN SELECT RAISE(ABORT, 'paper trades are append-only'); END;
CREATE TRIGGER paper_outcomes_no_update BEFORE UPDATE ON paper_outcomes
BEGIN SELECT RAISE(ABORT, 'paper outcomes are append-only'); END;
CREATE TRIGGER paper_outcomes_no_delete BEFORE DELETE ON paper_outcomes
BEGIN SELECT RAISE(ABORT, 'paper outcomes are append-only'); END;
