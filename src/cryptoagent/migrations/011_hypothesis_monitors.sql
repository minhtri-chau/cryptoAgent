-- 011: forward monitoring of registry hypotheses. A monitor is a hypothesis's signal (a defined
-- pattern plus optional conditions) on one market at one horizon. Each day the signal is active
-- on the latest candle, the daily task records a model prediction 'hyp_<hypothesis id>' (bound to
-- that candle like every prediction) and links it as forward evidence. A retired hypothesis's
-- monitors stop. Append-only.

CREATE TABLE hypothesis_monitors (
    id            INTEGER PRIMARY KEY,
    hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
    market        TEXT NOT NULL CHECK (market <> ''),
    pattern       TEXT NOT NULL CHECK (pattern <> ''),
    conditions    TEXT NOT NULL DEFAULT '[]',          -- JSON list, as in backtest specs
    horizon_days  INTEGER NOT NULL CHECK (horizon_days > 0),
    created_by    TEXT NOT NULL CHECK (created_by IN ('agent', 'user')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (hypothesis_id, market, horizon_days)
);

CREATE TRIGGER hypothesis_monitors_no_update BEFORE UPDATE ON hypothesis_monitors
BEGIN SELECT RAISE(ABORT, 'monitors are append-only; retire the hypothesis to stop them'); END;
CREATE TRIGGER hypothesis_monitors_no_delete BEFORE DELETE ON hypothesis_monitors
BEGIN SELECT RAISE(ABORT, 'monitors are append-only; retire the hypothesis to stop them'); END;
