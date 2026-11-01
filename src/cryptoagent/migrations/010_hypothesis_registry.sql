-- 010: hypothesis registry and concepts, for learning from outside analysts without inheriting
-- their conclusions. Everything is append-only.
--
-- * hypotheses: one row per distinct idea, stated neutrally. kind 'testable' is checked by trials;
--   kind 'observation' is a historical statement checked retrospectively against stored data.
--   A changed idea is a new hypothesis (refines_id), never an edit.
-- * source_mentions: who said it, where and when. Repeats by one creator are mentions, not
--   independent support; independent support counts distinct creators.
-- * hypothesis_alternatives: competing explanations and the benchmark that separates them.
-- * hypothesis_evidence: supporting or contradicting evidence, retrospective or forward.
-- * hypothesis_events: status history; only listed transitions per kind are allowed.
-- * concepts: analytical frameworks kept for reasoning, never promoted into rules.
-- * source_scenarios: a source's conditional, path-shaped prediction, with resolution criteria we
--   wrote. Monitored, but never scored as the creator's hit or miss (exact calls go to
--   external_calls instead).

CREATE TABLE hypotheses (
    id           INTEGER PRIMARY KEY,
    slug         TEXT NOT NULL UNIQUE CHECK (slug <> '' AND slug = lower(slug)),
    kind         TEXT NOT NULL CHECK (kind IN ('testable', 'observation')),
    subject      TEXT NOT NULL CHECK (subject <> '' AND subject = upper(subject)),
    statement    TEXT NOT NULL CHECK (statement <> ''),   -- precise, source-neutral definition
    conditions   TEXT NOT NULL CHECK (conditions <> ''),  -- market conditions it applies to
    required_data TEXT NOT NULL CHECK (required_data <> ''),
    methodology  TEXT NOT NULL CHECK (methodology <> ''),
    refines_id   INTEGER REFERENCES hypotheses(id),
    created_by   TEXT NOT NULL CHECK (created_by IN ('agent', 'user')),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE concepts (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE CHECK (slug <> '' AND slug = lower(slug)),
    name        TEXT NOT NULL CHECK (name <> ''),
    summary     TEXT NOT NULL CHECK (summary <> ''),      -- in our own words
    usage       TEXT NOT NULL CHECK (usage <> ''),          -- how the agent uses it
    limits      TEXT NOT NULL CHECK (limits <> ''),       -- what it is not evidence for
    supersedes_id INTEGER REFERENCES concepts(id),
    created_by  TEXT NOT NULL CHECK (created_by IN ('agent', 'user')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE source_mentions (
    id            INTEGER PRIMARY KEY,
    hypothesis_id INTEGER REFERENCES hypotheses(id),
    concept_id    INTEGER REFERENCES concepts(id),
    creator       TEXT NOT NULL CHECK (creator <> ''),
    url           TEXT NOT NULL CHECK (url LIKE 'http://%' OR url LIKE 'https://%'),
    published_on  TEXT NOT NULL,                          -- YYYY-MM-DD
    date_basis    TEXT NOT NULL CHECK (date_basis <> ''), -- how the date was established
    quote         TEXT,                                   -- 25 words at most, checked by the CLI
    recorded_by   TEXT NOT NULL CHECK (recorded_by IN ('agent', 'user')),
    recorded_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK ((hypothesis_id IS NULL) <> (concept_id IS NULL))
);
CREATE UNIQUE INDEX idx_mentions_hypothesis ON source_mentions(hypothesis_id, url) WHERE hypothesis_id IS NOT NULL;
CREATE UNIQUE INDEX idx_mentions_concept ON source_mentions(concept_id, url) WHERE concept_id IS NOT NULL;

CREATE TABLE hypothesis_alternatives (
    id            INTEGER PRIMARY KEY,
    hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
    explanation   TEXT NOT NULL CHECK (explanation <> ''),
    benchmark     TEXT NOT NULL CHECK (benchmark <> ''),  -- what result would separate the two
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE hypothesis_evidence (
    id            INTEGER PRIMARY KEY,
    hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
    direction     TEXT NOT NULL CHECK (direction IN ('supports', 'contradicts', 'neutral')),
    basis         TEXT NOT NULL CHECK (basis IN ('retrospective', 'forward')),
    summary       TEXT NOT NULL CHECK (summary <> ''),
    cite_kind     TEXT NOT NULL CHECK (cite_kind IN ('trial', 'prediction', 'claim', 'url', 'command')),
    cite_ref      TEXT NOT NULL CHECK (cite_ref <> ''),
    data_through  TEXT,                                   -- last data day a 'command' result used
    created_by    TEXT NOT NULL CHECK (created_by IN ('agent', 'user')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (cite_kind <> 'command' OR data_through IS NOT NULL),
    CHECK (basis <> 'forward' OR cite_kind = 'prediction')
);

-- Allowed status transitions per kind ('' = no event yet).
CREATE TABLE hypothesis_transitions (kind TEXT NOT NULL, from_status TEXT NOT NULL, to_status TEXT NOT NULL,
                                     PRIMARY KEY (kind, from_status, to_status));
INSERT INTO hypothesis_transitions VALUES
    ('testable', '', 'registered'),
    ('testable', 'registered', 'needs_data'), ('testable', 'registered', 'train_supported'),
    ('testable', 'registered', 'train_failed'), ('testable', 'registered', 'monitoring'),
    ('testable', 'registered', 'retired'),
    ('testable', 'needs_data', 'registered'), ('testable', 'needs_data', 'retired'),
    ('testable', 'train_supported', 'validated'), ('testable', 'train_supported', 'validation_failed'),
    ('testable', 'train_supported', 'retired'),
    ('testable', 'train_failed', 'retired'),
    ('testable', 'validation_failed', 'retired'),
    ('testable', 'validated', 'monitoring'), ('testable', 'validated', 'retired'),
    ('testable', 'monitoring', 'retired'),
    ('observation', '', 'registered'),
    ('observation', 'registered', 'consistent'), ('observation', 'registered', 'inconsistent'),
    ('observation', 'registered', 'mixed'), ('observation', 'registered', 'needs_data'),
    ('observation', 'registered', 'retired'),
    ('observation', 'needs_data', 'registered'), ('observation', 'needs_data', 'retired'),
    ('observation', 'consistent', 'inconsistent'), ('observation', 'consistent', 'mixed'),
    ('observation', 'consistent', 'retired'),
    ('observation', 'inconsistent', 'consistent'), ('observation', 'inconsistent', 'mixed'),
    ('observation', 'inconsistent', 'retired'),
    ('observation', 'mixed', 'consistent'), ('observation', 'mixed', 'inconsistent'),
    ('observation', 'mixed', 'retired');
CREATE TRIGGER hypothesis_transitions_frozen_insert BEFORE INSERT ON hypothesis_transitions
BEGIN SELECT RAISE(ABORT, 'allowed transitions change only through a migration'); END;
CREATE TRIGGER hypothesis_transitions_frozen_update BEFORE UPDATE ON hypothesis_transitions
BEGIN SELECT RAISE(ABORT, 'allowed transitions change only through a migration'); END;
CREATE TRIGGER hypothesis_transitions_frozen_delete BEFORE DELETE ON hypothesis_transitions
BEGIN SELECT RAISE(ABORT, 'allowed transitions change only through a migration'); END;

CREATE TABLE hypothesis_events (
    id            INTEGER PRIMARY KEY,
    hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
    status        TEXT NOT NULL,
    decided_by    TEXT NOT NULL CHECK (decided_by IN ('system', 'agent', 'user')),
    note          TEXT,
    evidence      TEXT,                                   -- JSON: trial ids and other references
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX idx_hypothesis_events ON hypothesis_events(hypothesis_id, id);

CREATE TRIGGER hypothesis_events_transition BEFORE INSERT ON hypothesis_events
WHEN NOT EXISTS (
    SELECT 1 FROM hypothesis_transitions
    WHERE kind = (SELECT kind FROM hypotheses WHERE id = NEW.hypothesis_id)
      AND to_status = NEW.status
      AND from_status = coalesce((SELECT status FROM hypothesis_events WHERE hypothesis_id = NEW.hypothesis_id
                                  ORDER BY id DESC LIMIT 1), ''))
BEGIN
    SELECT RAISE(ABORT, 'this status change is not allowed from the hypothesis''s current status');
END;

CREATE TABLE source_scenarios (
    id            INTEGER PRIMARY KEY,
    creator       TEXT NOT NULL CHECK (creator <> ''),
    url           TEXT NOT NULL CHECK (url LIKE 'http://%' OR url LIKE 'https://%'),
    published_on  TEXT NOT NULL,
    date_basis    TEXT NOT NULL CHECK (date_basis <> ''),
    description   TEXT NOT NULL CHECK (description <> ''),  -- the source's scenario, paraphrased
    resolution    TEXT NOT NULL CHECK (resolution <> ''),   -- our criteria: our interpretation, not theirs
    deadline      TEXT NOT NULL,
    hypothesis_id INTEGER REFERENCES hypotheses(id),
    recorded_by   TEXT NOT NULL CHECK (recorded_by IN ('agent', 'user')),
    recorded_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (deadline > published_on)
);

CREATE TABLE scenario_outcomes (
    scenario_id   INTEGER PRIMARY KEY REFERENCES source_scenarios(id),
    outcome       TEXT NOT NULL CHECK (outcome IN ('met', 'not_met', 'unresolvable')),
    note          TEXT NOT NULL CHECK (note <> ''),
    cite          TEXT NOT NULL CHECK (cite <> ''),          -- the command or record that settles it
    resolved_by   TEXT NOT NULL CHECK (resolved_by IN ('agent', 'user')),
    resolved_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TRIGGER hypotheses_no_update BEFORE UPDATE ON hypotheses
BEGIN SELECT RAISE(ABORT, 'hypotheses are append-only; register a refinement'); END;
CREATE TRIGGER hypotheses_no_delete BEFORE DELETE ON hypotheses
BEGIN SELECT RAISE(ABORT, 'hypotheses are append-only; register a refinement'); END;
CREATE TRIGGER concepts_no_update BEFORE UPDATE ON concepts
BEGIN SELECT RAISE(ABORT, 'concepts are append-only; add a superseding concept'); END;
CREATE TRIGGER concepts_no_delete BEFORE DELETE ON concepts
BEGIN SELECT RAISE(ABORT, 'concepts are append-only; add a superseding concept'); END;
CREATE TRIGGER source_mentions_no_update BEFORE UPDATE ON source_mentions
BEGIN SELECT RAISE(ABORT, 'source mentions are append-only'); END;
CREATE TRIGGER source_mentions_no_delete BEFORE DELETE ON source_mentions
BEGIN SELECT RAISE(ABORT, 'source mentions are append-only'); END;
CREATE TRIGGER hypothesis_alternatives_no_update BEFORE UPDATE ON hypothesis_alternatives
BEGIN SELECT RAISE(ABORT, 'alternatives are append-only'); END;
CREATE TRIGGER hypothesis_alternatives_no_delete BEFORE DELETE ON hypothesis_alternatives
BEGIN SELECT RAISE(ABORT, 'alternatives are append-only'); END;
CREATE TRIGGER hypothesis_evidence_no_update BEFORE UPDATE ON hypothesis_evidence
BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER hypothesis_evidence_no_delete BEFORE DELETE ON hypothesis_evidence
BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER hypothesis_events_no_update BEFORE UPDATE ON hypothesis_events
BEGIN SELECT RAISE(ABORT, 'hypothesis events are append-only'); END;
CREATE TRIGGER hypothesis_events_no_delete BEFORE DELETE ON hypothesis_events
BEGIN SELECT RAISE(ABORT, 'hypothesis events are append-only'); END;
CREATE TRIGGER source_scenarios_no_update BEFORE UPDATE ON source_scenarios
BEGIN SELECT RAISE(ABORT, 'scenarios are append-only'); END;
CREATE TRIGGER source_scenarios_no_delete BEFORE DELETE ON source_scenarios
BEGIN SELECT RAISE(ABORT, 'scenarios are append-only'); END;
CREATE TRIGGER scenario_outcomes_no_update BEFORE UPDATE ON scenario_outcomes
BEGIN SELECT RAISE(ABORT, 'scenario outcomes are append-only'); END;
CREATE TRIGGER scenario_outcomes_no_delete BEFORE DELETE ON scenario_outcomes
BEGIN SELECT RAISE(ABORT, 'scenario outcomes are append-only'); END;
