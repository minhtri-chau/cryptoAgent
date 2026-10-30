-- 001: market data core. Timestamps are ISO-8601 UTC text ('YYYY-MM-DDTHH:MM:SSZ').
-- Prices are REAL: crypto prices span ~1e-8 to ~1e5, so integer cents don't fit.

CREATE TABLE sources (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,              -- collector name, e.g. 'coinbase', 'bitstamp', 'coinmetrics'
    kind        TEXT NOT NULL CHECK (kind IN ('exchange', 'reference')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE assets (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL UNIQUE CHECK (symbol <> '' AND symbol = upper(symbol)),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- A tradable pair on one exchange. history_start_found_at is set once a backfill has
-- walked back past the first candle, so later runs only extend forward.
CREATE TABLE markets (
    id                     INTEGER PRIMARY KEY,
    source_id              INTEGER NOT NULL REFERENCES sources(id),
    base_asset_id          INTEGER NOT NULL REFERENCES assets(id),
    quote                  TEXT NOT NULL CHECK (quote = upper(quote)),
    symbol                 TEXT NOT NULL,          -- unified symbol, e.g. 'BTC/USD'
    venue_id               TEXT NOT NULL,          -- the exchange's own id, e.g. 'BTC-USD'
    first_seen_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    history_start_found_at TEXT,
    UNIQUE (source_id, symbol)
);

CREATE TABLE ingest_runs (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES sources(id),
    started_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'succeeded', 'failed')),
    options     TEXT,                              -- JSON of the options the run was given
    row_count   INTEGER CHECK (row_count IS NULL OR row_count >= 0),
    error       TEXT
);

-- Listing status as seen at each run; a row is added only when the status changes.
-- Point-in-time universe membership is rebuilt from this, so it is append-only.
CREATE TABLE market_status_history (
    id            INTEGER PRIMARY KEY,
    market_id     INTEGER NOT NULL REFERENCES markets(id),
    status        TEXT NOT NULL,                   -- exchange's own value, e.g. 'online', 'delisted'
    active        INTEGER NOT NULL CHECK (active IN (0, 1)),
    observed_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ingest_run_id INTEGER REFERENCES ingest_runs(id)
);
CREATE INDEX idx_market_status_market ON market_status_history(market_id, observed_at);

CREATE TRIGGER market_status_history_no_update BEFORE UPDATE ON market_status_history
BEGIN SELECT RAISE(ABORT, 'market_status_history is append-only'); END;
CREATE TRIGGER market_status_history_no_delete BEFORE DELETE ON market_status_history
BEGIN SELECT RAISE(ABORT, 'market_status_history is append-only'); END;

-- Completed candles only: ts is the period start, and a candle is stored only after its
-- period has ended. Invalid candles never get here; collectors record them in quality_issues.
CREATE TABLE candles (
    market_id     INTEGER NOT NULL REFERENCES markets(id),
    timeframe     TEXT NOT NULL CHECK (timeframe IN ('1d')),
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

-- Independent reference prices (e.g. Coin Metrics PriceUSD) used to cross-check exchange closes.
-- day is the UTC date the value applies to.
CREATE TABLE reference_prices (
    asset_id      INTEGER NOT NULL REFERENCES assets(id),
    source_id     INTEGER NOT NULL REFERENCES sources(id),
    metric        TEXT NOT NULL,
    day           TEXT NOT NULL,
    value         REAL NOT NULL CHECK (value > 0),
    ingest_run_id INTEGER NOT NULL REFERENCES ingest_runs(id),
    collected_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (asset_id, source_id, metric, day)
) WITHOUT ROWID;

-- Data problems, found at ingest (rejected candles) or by `quality` checks over stored data.
-- One row per (check, subject, ts), so re-running checks does not duplicate findings.
CREATE TABLE quality_issues (
    id            INTEGER PRIMARY KEY,
    check_name    TEXT NOT NULL CHECK (check_name IN
                    ('invalid_ohlc', 'missing_candle', 'outlier_return', 'reference_mismatch')),
    market_id     INTEGER NOT NULL REFERENCES markets(id),
    ts            TEXT NOT NULL,
    detail       TEXT NOT NULL,                   -- JSON with the values involved
    ingest_run_id INTEGER REFERENCES ingest_runs(id),
    detected_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE UNIQUE INDEX idx_quality_issues_unique ON quality_issues(check_name, market_id, ts);
