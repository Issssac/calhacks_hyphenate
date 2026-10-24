-- The central event log every agent appends to.
-- Rows are never updated or deleted: the triggers below refuse it, and each
-- row's hash covers the previous row's, so `ledgerdb verify` catches tampering
-- even if someone drops the triggers.

CREATE TABLE IF NOT EXISTS events (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT    NOT NULL,              -- UTC ISO-8601
    actor            TEXT    NOT NULL,              -- on whose authority (agent id, or human:<name>)
    recorded_by      TEXT    NOT NULL,              -- who actually wrote the row
    role             TEXT    NOT NULL,              -- role from roles.toml
    run_id           TEXT,
    workflow         TEXT,                          -- harness name that produced it
    workflow_version INTEGER,
    kind             TEXT    NOT NULL,              -- e.g. so.drafted
    entity           TEXT    NOT NULL,              -- type:id, e.g. so:SO-1001
    entity_type      TEXT    NOT NULL,              -- the type part of entity
    payload          TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(payload)),
    sources          TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(sources)),
    links            TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(links)),
    prev_hash        TEXT    NOT NULL,
    hash             TEXT    NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS events_entity ON events (entity, seq);
CREATE INDEX IF NOT EXISTS events_kind   ON events (kind, seq);
CREATE INDEX IF NOT EXISTS events_type   ON events (entity_type, seq);

CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events is append-only: write a new event instead');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events is append-only: write a reversing event instead');
END;
