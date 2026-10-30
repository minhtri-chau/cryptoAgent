-- 003: trial registry. Every backtest run is recorded, failures included, so a good result
-- can be weighed against how many attempts produced it. Append-only.

CREATE TABLE trials (
    id                INTEGER PRIMARY KEY,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    created_by        TEXT NOT NULL CHECK (created_by <> ''),
    family            TEXT NOT NULL CHECK (family <> ''),     -- groups variants of one idea
    hypothesis        TEXT NOT NULL CHECK (hypothesis <> ''),
    spec              TEXT NOT NULL,                          -- canonical JSON: market, pattern, conditions, exit
    spec_hash         TEXT NOT NULL,
    market            TEXT NOT NULL,
    period            TEXT NOT NULL CHECK (period IN ('train', 'validation', 'test')),
    period_start      TEXT,
    period_end        TEXT,
    data_through      TEXT NOT NULL,                          -- last candle day the run used
    costs             TEXT NOT NULL,                          -- JSON
    trades            INTEGER NOT NULL CHECK (trades >= 0),
    mean_return       REAL,
    results           TEXT NOT NULL                           -- JSON summary (without the trade list)
);
CREATE INDEX idx_trials_family ON trials(family);
CREATE INDEX idx_trials_spec ON trials(spec_hash, period);

-- The test period is sealed: one look per specification, and only after validation.
CREATE UNIQUE INDEX idx_trials_test_once ON trials(spec_hash) WHERE period = 'test';
CREATE TRIGGER trials_test_after_validation BEFORE INSERT ON trials
WHEN NEW.period = 'test'
 AND NOT EXISTS (SELECT 1 FROM trials WHERE spec_hash = NEW.spec_hash AND period = 'validation')
BEGIN
    SELECT RAISE(ABORT, 'the test period needs a validation trial of the same specification first');
END;

CREATE TRIGGER trials_no_update BEFORE UPDATE ON trials
BEGIN SELECT RAISE(ABORT, 'trials are append-only'); END;
CREATE TRIGGER trials_no_delete BEFORE DELETE ON trials
BEGIN SELECT RAISE(ABORT, 'trials are append-only'); END;
