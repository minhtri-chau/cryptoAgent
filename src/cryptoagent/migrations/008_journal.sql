-- 008: research journal. One entry per daily briefing (and any note the user adds), so each
-- session starts from what was concluded last time rather than from memory. Append-only.

CREATE TABLE journal (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    kind        TEXT NOT NULL CHECK (kind IN ('briefing', 'note')),
    author      TEXT NOT NULL CHECK (author IN ('agent', 'user')),
    summary     TEXT NOT NULL CHECK (summary <> ''),
    report_path TEXT,
    evidence    TEXT                                  -- JSON: prediction, recommendation, trial, claim ids
);
CREATE INDEX idx_journal_created ON journal(created_at);

CREATE TRIGGER journal_no_update BEFORE UPDATE ON journal
BEGIN SELECT RAISE(ABORT, 'the journal is append-only'); END;
CREATE TRIGGER journal_no_delete BEFORE DELETE ON journal
BEGIN SELECT RAISE(ABORT, 'the journal is append-only'); END;
