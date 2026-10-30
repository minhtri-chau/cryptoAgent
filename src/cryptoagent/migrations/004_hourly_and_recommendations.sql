-- 004: hourly candles, per-timeframe backfill state, and the recommendation ledger.

-- Backfill progress is per market and timeframe (daily and hourly histories start differently).
CREATE TABLE market_backfill (
    market_id              INTEGER NOT NULL REFERENCES markets(id),
    timeframe              TEXT NOT NULL,
    history_start_found_at TEXT NOT NULL,
    PRIMARY KEY (market_id, timeframe)
);
INSERT INTO market_backfill (market_id, timeframe, history_start_found_at)
    SELECT id, '1d', history_start_found_at FROM markets WHERE history_start_found_at IS NOT NULL;
ALTER TABLE markets DROP COLUMN history_start_found_at;

-- Rebuild candles to allow '1h' (SQLite cannot alter a CHECK constraint). Legacy rename mode
-- keeps the triggers that refer to `candles` by name pointing at the rebuilt table.
PRAGMA legacy_alter_table = ON;
CREATE TABLE candles_new (
    market_id     INTEGER NOT NULL REFERENCES markets(id),
    timeframe     TEXT NOT NULL CHECK (timeframe IN ('1d', '1h')),
    ts            TEXT NOT NULL,
    open          REAL NOT NULL CHECK (open > 0),
    high          REAL NOT NULL,
    low           REAL NOT NULL CHECK (low > 0),
    close         REAL NOT NULL CHECK (close > 0),
    volume        REAL NOT NULL CHECK (volume >= 0),
    ingest_run_id INTEGER NOT NULL REFERENCES ingest_runs(id),
    collected_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (market_id, timeframe, ts),
    CHECK (high >= open AND high >= close AND high >= low AND low <= open AND low <= close)
) WITHOUT ROWID;
INSERT INTO candles_new SELECT market_id, timeframe, ts, open, high, low, close, volume, ingest_run_id, collected_at
    FROM candles;
DROP TABLE candles;
ALTER TABLE candles_new RENAME TO candles;
PRAGMA legacy_alter_table = OFF;

-- Recommendations: structured, append-only, bound to the latest stored daily candle.
CREATE TABLE recommendations (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    created_by    TEXT NOT NULL CHECK (created_by <> ''),
    market_id     INTEGER NOT NULL REFERENCES markets(id),
    as_of         TEXT NOT NULL,
    as_of_close   REAL NOT NULL CHECK (as_of_close > 0),
    horizon_days  INTEGER NOT NULL CHECK (horizon_days > 0),
    target_day    TEXT NOT NULL,
    action        TEXT NOT NULL CHECK (action IN ('buy', 'sell', 'hold', 'wait', 'no_trade')),
    entry         TEXT NOT NULL CHECK (entry <> ''),        -- entry conditions, or why there is none
    invalidation  TEXT NOT NULL CHECK (invalidation <> ''),
    stop_atr      REAL CHECK (stop_atr IS NULL OR stop_atr > 0),
    target_atr    REAL CHECK (target_atr IS NULL OR target_atr > 0),
    atr           REAL CHECK (atr IS NULL OR atr > 0),      -- ATR14 on as_of, for the stop and target levels
    risks         TEXT NOT NULL CHECK (risks <> ''),
    limitations   TEXT NOT NULL CHECK (limitations <> ''),
    evidence      TEXT,                                     -- JSON: trial ids, prediction ids, commands
    prediction_id INTEGER REFERENCES predictions(id),
    report_path   TEXT,
    CHECK (target_day = date(as_of, '+' || horizon_days || ' days')),
    CHECK (action <> 'buy' OR (stop_atr IS NOT NULL AND atr IS NOT NULL))
);

CREATE TRIGGER recommendations_bind_latest_candle BEFORE INSERT ON recommendations
WHEN NEW.as_of IS NOT (SELECT substr(max(ts), 1, 10) FROM candles WHERE market_id = NEW.market_id AND timeframe = '1d')
  OR NEW.as_of_close IS NOT (SELECT close FROM candles WHERE market_id = NEW.market_id AND timeframe = '1d'
                             AND ts = NEW.as_of || 'T00:00:00Z')
BEGIN
    SELECT RAISE(ABORT, 'a recommendation must use the latest stored candle and its close');
END;

CREATE TABLE recommendation_outcomes (
    recommendation_id INTEGER PRIMARY KEY REFERENCES recommendations(id),
    resolved_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    status            TEXT NOT NULL CHECK (status IN ('resolved', 'unresolvable')),
    market_return     REAL,          -- close on target_day / close on as_of - 1
    trade_return      REAL,          -- buys only: simulated with costs
    exit_reason       TEXT,
    entry_price       REAL,
    exit_price        REAL,
    note              TEXT,
    CHECK ((status = 'resolved') = (market_return IS NOT NULL))
);

CREATE TRIGGER recommendations_no_update BEFORE UPDATE ON recommendations
BEGIN SELECT RAISE(ABORT, 'recommendations are append-only'); END;
CREATE TRIGGER recommendations_no_delete BEFORE DELETE ON recommendations
BEGIN SELECT RAISE(ABORT, 'recommendations are append-only'); END;
CREATE TRIGGER recommendation_outcomes_no_update BEFORE UPDATE ON recommendation_outcomes
BEGIN SELECT RAISE(ABORT, 'recommendation outcomes are append-only'); END;
CREATE TRIGGER recommendation_outcomes_no_delete BEFORE DELETE ON recommendation_outcomes
BEGIN SELECT RAISE(ABORT, 'recommendation outcomes are append-only'); END;
