-- BST Social Lens — audit trail (SLL-PROP-DATA-001G §5). ADDITIVE; depends on nothing unfrozen, applied at startup.
-- Append-only: no UPDATE/DELETE path exists in the application; nothing here is ever purged.
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS audit.events (
    event_id        BIGSERIAL PRIMARY KEY,
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor           TEXT NOT NULL,                          -- user/token id; 'system:<component>' for automated actions
    role            TEXT NOT NULL CHECK (role IN ('viewer','reviewer','reviewer_contacts','publisher','system')),
    queue           TEXT CHECK (queue IN ('extraction','location','match','exceptions','publish','geo','contacts')),
    item_table      TEXT NOT NULL,                          -- e.g. 'extract.claims'
    item_id         TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('CONFIRM','CORRECT','REJECT','DEFER','MERGE','SPLIT','ALIAS','CONTACT_REVEAL','PUBLISH','WITHDRAW','ASSEMBLE','IMPORT')),
    before          JSONB,
    after           JSONB,
    reason          TEXT,
    batch_id        TEXT,                                   -- groups one CSV import / one publish
    CHECK (action <> 'CONTACT_REVEAL' OR role = 'reviewer_contacts')
);
CREATE INDEX IF NOT EXISTS idx_audit_item  ON audit.events (item_table, item_id);
CREATE INDEX IF NOT EXISTS idx_audit_batch ON audit.events (batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_at    ON audit.events (at DESC);

-- HK-001 §4: every Housekeeper action is one audit row (actor 'system:housekeeper', role 'system', action 'HOUSEKEEP').
-- Vocabulary widened additively: the original CHECKs are replaced by supersets (no rows become invalid).
ALTER TABLE audit.events DROP CONSTRAINT IF EXISTS events_action_check;
ALTER TABLE audit.events ADD CONSTRAINT events_action_check CHECK (action IN ('CONFIRM','CORRECT','REJECT','DEFER','MERGE','SPLIT','ALIAS','CONTACT_REVEAL','PUBLISH','WITHDRAW','ASSEMBLE','IMPORT','HOUSEKEEP'));
ALTER TABLE audit.events DROP CONSTRAINT IF EXISTS events_queue_check;
ALTER TABLE audit.events ADD CONSTRAINT events_queue_check CHECK (queue IN ('extraction','location','match','exceptions','publish','geo','contacts','housekeeping'));

