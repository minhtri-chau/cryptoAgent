-- 005: persistent research knowledge. Facts and hypotheses are stored apart by status; a fact
-- needs at least one citation and a hypothesis needs a confidence. Rows are append-only: a
-- correction is a new claim whose supersedes_id points at the old one.

CREATE TABLE claims (
    id            INTEGER PRIMARY KEY,
    topic         TEXT NOT NULL,
    subject       TEXT NOT NULL CHECK (subject <> '' AND subject = upper(subject)),  -- 'MARKET' or a coin, e.g. 'ETH'
    statement     TEXT NOT NULL CHECK (statement <> ''),
    status        TEXT NOT NULL CHECK (status IN ('fact', 'hypothesis')),
    confidence    REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    as_of         TEXT NOT NULL,                    -- date the statement is true as of
    review_after  TEXT,                             -- re-check after this date (fees, policies, unlocks)
    created_by    TEXT NOT NULL CHECK (created_by IN ('agent', 'user')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    supersedes_id INTEGER REFERENCES claims(id),
    CHECK ((status = 'hypothesis') = (confidence IS NOT NULL))
);
CREATE INDEX idx_claims_topic ON claims(topic);
CREATE INDEX idx_claims_subject ON claims(subject);

CREATE TABLE claim_citations (
    id          INTEGER PRIMARY KEY,
    claim_id    INTEGER NOT NULL REFERENCES claims(id),
    kind        TEXT NOT NULL CHECK (kind IN ('url', 'trial', 'prediction', 'claim')),
    ref         TEXT NOT NULL CHECK (ref <> ''),
    excerpt     TEXT,
    accessed_at TEXT
);
CREATE INDEX idx_claim_citations_claim ON claim_citations(claim_id);

-- Claims that have not been superseded.
CREATE VIEW current_claims AS
SELECT c.* FROM claims c
WHERE NOT EXISTS (SELECT 1 FROM claims n WHERE n.supersedes_id = c.id);

CREATE TRIGGER claims_no_update BEFORE UPDATE ON claims
BEGIN SELECT RAISE(ABORT, 'claims are append-only; add a superseding claim'); END;
CREATE TRIGGER claims_no_delete BEFORE DELETE ON claims
BEGIN SELECT RAISE(ABORT, 'claims are append-only; add a superseding claim'); END;
CREATE TRIGGER claim_citations_no_update BEFORE UPDATE ON claim_citations
BEGIN SELECT RAISE(ABORT, 'citations are append-only'); END;
CREATE TRIGGER claim_citations_no_delete BEFORE DELETE ON claim_citations
BEGIN SELECT RAISE(ABORT, 'citations are append-only'); END;
