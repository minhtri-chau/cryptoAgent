-- 002: prediction ledger. A prediction is "the probability that the close on target_day is
-- above the close on as_of". Predictions and outcomes are append-only.

CREATE TABLE predictions (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    market_id     INTEGER NOT NULL REFERENCES markets(id),
    as_of         TEXT NOT NULL,                 -- 'YYYY-MM-DD' of the reference candle
    as_of_close   REAL NOT NULL CHECK (as_of_close > 0),
    horizon_days  INTEGER NOT NULL CHECK (horizon_days > 0),
    target_day    TEXT NOT NULL,
    prob_up       REAL NOT NULL CHECK (prob_up >= 0 AND prob_up <= 1),
    source        TEXT NOT NULL CHECK (source IN ('model', 'judgment')),
    model         TEXT NOT NULL CHECK (model <> ''),  -- model name and version, or the author of a judgment
    rationale     TEXT,
    evidence      TEXT,                          -- JSON: what the prediction was based on
    UNIQUE (market_id, as_of, horizon_days, source, model),
    CHECK (target_day = date(as_of, '+' || horizon_days || ' days'))
);
CREATE INDEX idx_predictions_target ON predictions(market_id, target_day);

-- No back-dating: the reference candle must be the latest one stored for the market, and
-- its close must be the stored close.
CREATE TRIGGER predictions_bind_latest_candle BEFORE INSERT ON predictions
WHEN NEW.as_of IS NOT (SELECT substr(max(ts), 1, 10) FROM candles WHERE market_id = NEW.market_id AND timeframe = '1d')
  OR NEW.as_of_close IS NOT (SELECT close FROM candles WHERE market_id = NEW.market_id AND timeframe = '1d'
                             AND ts = NEW.as_of || 'T00:00:00Z')
BEGIN
    SELECT RAISE(ABORT, 'a prediction must use the latest stored candle and its close');
END;

CREATE TABLE outcomes (
    prediction_id   INTEGER PRIMARY KEY REFERENCES predictions(id),
    resolved_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    status          TEXT NOT NULL CHECK (status IN ('resolved', 'unresolvable')),
    target_close    REAL CHECK (target_close IS NULL OR target_close > 0),
    realized_return REAL,
    up              INTEGER CHECK (up IS NULL OR up IN (0, 1)),
    brier           REAL CHECK (brier IS NULL OR (brier >= 0 AND brier <= 1)),
    note            TEXT,
    CHECK ((status = 'resolved') = (target_close IS NOT NULL AND up IS NOT NULL AND brier IS NOT NULL))
);

-- Outcomes may only be written once the target day's candle could exist.
CREATE TRIGGER outcomes_not_before_target BEFORE INSERT ON outcomes
WHEN NEW.status = 'resolved' AND NOT EXISTS (
    SELECT 1 FROM predictions p JOIN candles c ON c.market_id = p.market_id AND c.timeframe = '1d'
     AND c.ts = p.target_day || 'T00:00:00Z' AND c.close = NEW.target_close
    WHERE p.id = NEW.prediction_id)
BEGIN
    SELECT RAISE(ABORT, 'an outcome must use the stored close of the target day');
END;

CREATE TRIGGER predictions_no_update BEFORE UPDATE ON predictions
BEGIN SELECT RAISE(ABORT, 'predictions are append-only'); END;
CREATE TRIGGER predictions_no_delete BEFORE DELETE ON predictions
BEGIN SELECT RAISE(ABORT, 'predictions are append-only'); END;
CREATE TRIGGER outcomes_no_update BEFORE UPDATE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are append-only'); END;
CREATE TRIGGER outcomes_no_delete BEFORE DELETE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are append-only'); END;
