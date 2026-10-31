-- 009: dated market calls by outside commentators (YouTube creators, analysts), scored the same way
-- as the agent's own: against what happened and against a naive base rate. Append-only.
--
-- A call is anchored to the close on the day it was published, which must be a stored candle, so
-- a call is never judged from a price it could not have seen. 'forward' calls were recorded within
-- 7 days of publication; 'historical' ones were found later and are reported separately, because
-- the channels someone follows today were chosen after their past calls were known.

CREATE TABLE external_calls (
    id              INTEGER PRIMARY KEY,
    recorded_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    recorded_by     TEXT NOT NULL CHECK (recorded_by IN ('agent', 'user')),
    creator         TEXT NOT NULL CHECK (creator <> ''),
    source_url      TEXT NOT NULL CHECK (source_url LIKE 'http://%' OR source_url LIKE 'https://%'),
    quote           TEXT,                        -- 25 words at most, checked by the CLI
    published_on    TEXT NOT NULL,               -- 'YYYY-MM-DD' the call was made public
    market_id       INTEGER NOT NULL REFERENCES markets(id),
    published_close REAL NOT NULL CHECK (published_close > 0),
    call_type       TEXT NOT NULL CHECK (call_type IN ('up', 'down', 'reach_above', 'reach_below')),
    target          REAL CHECK (target IS NULL OR target > 0),
    deadline        TEXT NOT NULL,               -- 'YYYY-MM-DD' by which the call must come true
    timing          TEXT NOT NULL CHECK (timing IN ('forward', 'historical')),
    CHECK ((call_type IN ('reach_above', 'reach_below')) = (target IS NOT NULL)),
    CHECK (deadline > published_on),
    UNIQUE (creator, source_url, market_id, call_type, deadline)
);

-- The anchor must be the stored close of the publication day.
CREATE TRIGGER external_calls_anchor BEFORE INSERT ON external_calls
WHEN NEW.published_close IS NOT (SELECT close FROM candles WHERE market_id = NEW.market_id AND timeframe = '1d'
                                 AND ts = NEW.published_on || 'T00:00:00Z')
BEGIN
    SELECT RAISE(ABORT, 'a call must be anchored to the stored close of its publication day');
END;

CREATE TABLE external_outcomes (
    call_id       INTEGER PRIMARY KEY REFERENCES external_calls(id),
    resolved_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    status        TEXT NOT NULL CHECK (status IN ('resolved', 'unresolvable')),
    hit           INTEGER CHECK (hit IS NULL OR hit IN (0, 1)),
    deadline_close REAL,
    extreme       REAL,          -- highest high (reach_above) or lowest low (reach_below) through the deadline
    base_rate     REAL,          -- naive chance of the same outcome, from history before publication
    note          TEXT,
    CHECK ((status = 'resolved') = (hit IS NOT NULL))
);

CREATE TRIGGER external_calls_no_update BEFORE UPDATE ON external_calls
BEGIN SELECT RAISE(ABORT, 'external calls are append-only'); END;
CREATE TRIGGER external_calls_no_delete BEFORE DELETE ON external_calls
BEGIN SELECT RAISE(ABORT, 'external calls are append-only'); END;
CREATE TRIGGER external_outcomes_no_update BEFORE UPDATE ON external_outcomes
BEGIN SELECT RAISE(ABORT, 'external outcomes are append-only'); END;
CREATE TRIGGER external_outcomes_no_delete BEFORE DELETE ON external_outcomes
BEGIN SELECT RAISE(ABORT, 'external outcomes are append-only'); END;
