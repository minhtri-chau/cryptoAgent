-- 006: protocol fundamentals (DefiLlama), stored as daily point-in-time snapshots only.
-- No history is backfilled: a backfilled value would only be "known" from the day we fetched
-- it, and DefiLlama history can be revised. Kept private (see the decision log).

-- A protocol family as DefiLlama groups it (e.g. 'parent#aave' for Aave V2 + V3), with the
-- token symbol its member protocols report.
CREATE TABLE fundamental_entities (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES sources(id),
    key           TEXT NOT NULL,
    name          TEXT NOT NULL,
    symbol        TEXT NOT NULL CHECK (symbol <> '' AND symbol = upper(symbol)),
    category      TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (source_id, key)
);
CREATE INDEX idx_fundamental_entities_symbol ON fundamental_entities(symbol);

-- One value per entity, metric and UTC collection day; a rerun on the same day adds nothing.
-- tvl and mcap are as reported at collection time; fees_*/revenue_* are DefiLlama's trailing
-- 24-hour and 30-day totals at collection time.
CREATE TABLE fundamental_obs (
    entity_id     INTEGER NOT NULL REFERENCES fundamental_entities(id),
    metric        TEXT NOT NULL CHECK (metric IN ('tvl', 'mcap', 'fees_24h', 'fees_30d', 'revenue_24h',
                                                  'revenue_30d')),
    day           TEXT NOT NULL,
    value         REAL NOT NULL CHECK (value >= 0),
    ingest_run_id INTEGER NOT NULL REFERENCES ingest_runs(id),
    collected_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (entity_id, metric, day)
) WITHOUT ROWID;

CREATE TRIGGER fundamental_obs_no_update BEFORE UPDATE ON fundamental_obs
BEGIN SELECT RAISE(ABORT, 'fundamental observations are append-only'); END;
CREATE TRIGGER fundamental_obs_no_delete BEFORE DELETE ON fundamental_obs
BEGIN SELECT RAISE(ABORT, 'fundamental observations are append-only'); END;
