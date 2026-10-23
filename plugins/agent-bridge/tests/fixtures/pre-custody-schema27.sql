-- Frozen pre-custody schema 27 and synthetic retained history.
-- Independent of Database, current schema constants and Git at test runtime.
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version VALUES (27);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, agent_name TEXT, caller_id TEXT,
    caller_session_id TEXT, target_dir TEXT,
    target_type TEXT NOT NULL DEFAULT 'local', target_json TEXT,
    status TEXT NOT NULL DEFAULT 'created', pid INTEGER, acp_session_id TEXT,
    config_json TEXT, context_size INTEGER, context_used INTEGER,
    usage_model TEXT, last_usage_at REAL, predecessor_id TEXT,
    successor_id TEXT, handoff_at REAL,
    background_recovery_enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE workspace_embodiments (
    key_digest TEXT PRIMARY KEY, facts_digest TEXT NOT NULL, state TEXT NOT NULL,
    worktree_json TEXT, session_id TEXT NOT NULL, native_session_id TEXT, turn_index INTEGER
);
CREATE TABLE provider_embodiments (
    key_digest TEXT PRIMARY KEY, facts_digest TEXT NOT NULL, state TEXT NOT NULL,
    session_id TEXT NOT NULL, native_session_id TEXT
);
CREATE TABLE remote_acp_relationships (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
    recovery_json TEXT NOT NULL, requested_native_id TEXT, native_id TEXT,
    claim_json TEXT, phase TEXT NOT NULL, uncertain INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE remote_acp_relationship_authority (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1), id TEXT NOT NULL
);
INSERT INTO remote_acp_relationship_authority
    VALUES (1,'0123456789abcdef0123456789abcdef');
CREATE INDEX idx_remote_acp_relationship_ref
    ON remote_acp_relationships(json_extract(claim_json,'$.ref'));
CREATE TABLE turns (
    session_id TEXT NOT NULL, turn_index INTEGER NOT NULL, prompt TEXT NOT NULL,
    response_text TEXT DEFAULT '', thought_text TEXT DEFAULT '', stop_reason TEXT,
    tool_calls_json TEXT DEFAULT '[]', started_at REAL, completed_at REAL,
    acquisition_delivery_id TEXT, native_admission_json TEXT,
    PRIMARY KEY (session_id,turn_index),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE UNIQUE INDEX idx_turn_acquisition_delivery
    ON turns(acquisition_delivery_id) WHERE acquisition_delivery_id IS NOT NULL;
CREATE TABLE events (
    session_id TEXT NOT NULL, event_id INTEGER NOT NULL, event_type TEXT NOT NULL,
    data_json TEXT NOT NULL, timestamp REAL NOT NULL,
    PRIMARY KEY (session_id,event_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE TABLE delivery_cursors (
    caller_id TEXT NOT NULL, session_id TEXT NOT NULL,
    last_acked_id INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
    PRIMARY KEY (caller_id,session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE TABLE delivery_cursor_invalidations (
    caller_id TEXT NOT NULL, session_id TEXT NOT NULL,
    prior_last_acked_id INTEGER NOT NULL, prior_head_id INTEGER NOT NULL,
    prior_continuity_id TEXT, current_continuity_id TEXT, invalidated_at REAL NOT NULL,
    PRIMARY KEY (caller_id,session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE TABLE live_sessions (
    session_id TEXT PRIMARY KEY, machine TEXT, cwd TEXT, worktree_id TEXT,
    repo TEXT, branch TEXT, pid INTEGER, role TEXT, driven_by TEXT,
    status TEXT NOT NULL DEFAULT 'live', turn_state TEXT, last_activity_at REAL,
    latest_progress TEXT, cli_mode INTEGER NOT NULL DEFAULT 0, venue TEXT,
    process_started_at REAL, registered_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE live_session_aliases (
    alias_session_id TEXT PRIMARY KEY, target_session_id TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX idx_live_session_aliases_target ON live_session_aliases(target_session_id);
CREATE TRIGGER live_sessions_retired_id_fence BEFORE INSERT ON live_sessions
    WHEN EXISTS (SELECT 1 FROM live_session_aliases WHERE alias_session_id=NEW.session_id)
    BEGIN SELECT RAISE(IGNORE); END;
CREATE TRIGGER live_sessions_drop_orphaned_aliases AFTER DELETE ON live_sessions
    BEGIN DELETE FROM live_session_aliases WHERE target_session_id=OLD.session_id; END;
CREATE TABLE live_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, sender TEXT NOT NULL,
    body TEXT NOT NULL, reply_to TEXT, kind TEXT NOT NULL DEFAULT 'prompt',
    delivery TEXT NOT NULL DEFAULT 'queue', idempotency_key TEXT, created_at REAL NOT NULL,
    delivered_at REAL, consumed_at REAL, consumption_tracked INTEGER NOT NULL DEFAULT 0,
    claimed_at REAL, outcome TEXT
);
CREATE UNIQUE INDEX idx_live_messages_idempotency ON live_messages(idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE TABLE worktree_ownership (
    worktree_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    reserved_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE cli_mode_reservations (
    worktree_id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL, created_at REAL NOT NULL,
    expires_at REAL NOT NULL, claimed_by_session_id TEXT, venue TEXT
);
CREATE TABLE pending_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, caller_id TEXT,
    prompt TEXT NOT NULL, created_at REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX idx_sessions_status ON sessions(status);
CREATE INDEX idx_events_session_id ON events(session_id,event_id);
CREATE INDEX idx_live_sessions_worktree ON live_sessions(worktree_id);
CREATE INDEX idx_live_messages_pending ON live_messages(session_id,delivered_at,id);
CREATE INDEX idx_pending_prompts_session ON pending_prompts(session_id,id);

INSERT INTO sessions
    (id,name,caller_id,status,acp_session_id,background_recovery_enabled,created_at,updated_at)
    VALUES ('retained','retained','fixture-caller','ended','native-retained',0,1,2);
INSERT INTO turns
    (session_id,turn_index,prompt,response_text,stop_reason,started_at,completed_at,
     acquisition_delivery_id)
    VALUES ('retained',0,'original prompt','original result','completed',1,2,'original-delivery');
INSERT INTO events VALUES
    ('retained',1,'user_message','{"content":"original prompt"}',1);
INSERT INTO delivery_cursors VALUES ('fixture-caller','retained',1,2);
INSERT INTO pending_prompts (session_id,caller_id,prompt,created_at)
    VALUES ('retained','fixture-caller','retained follow-up',2);
