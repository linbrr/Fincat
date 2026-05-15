-- L1 Resources: conversations table (SQLite mirror of conversations.jsonl for FK references)
CREATE TABLE IF NOT EXISTS conversations (
    resource_id  TEXT PRIMARY KEY,
    content_hash TEXT,
    timestamp    TEXT NOT NULL,
    session_id   TEXT,
    message_id   TEXT
);

-- L2 Memory: structured item attributes
CREATE TABLE IF NOT EXISTS memory_item (
    item_id          TEXT PRIMARY KEY,
    resource_id      TEXT,
    category_id      TEXT,
    memory_type      TEXT NOT NULL,
    summary          TEXT NOT NULL,
    content          TEXT,
    importance_score REAL DEFAULT 0.5,
    entities         TEXT DEFAULT '[]',
    tags             TEXT DEFAULT '[]',
    created_at       TEXT NOT NULL,
    embedded_at      TEXT,
    last_accessed_at TEXT,
    access_count     INTEGER DEFAULT 0,
    is_active        INTEGER DEFAULT 1,
    extra            TEXT DEFAULT '{}'
);

-- L2 Memory: FAISS vector ↔ item mapping
CREATE TABLE IF NOT EXISTS vector_mapping (
    faiss_index  INTEGER PRIMARY KEY,
    item_id      TEXT NOT NULL UNIQUE,
    summary_hash TEXT,
    embedded_at  TEXT NOT NULL,
    model_name   TEXT NOT NULL DEFAULT 'bge-small-zh-v1.5'
);

CREATE INDEX IF NOT EXISTS idx_memory_item_resource ON memory_item(resource_id);
CREATE INDEX IF NOT EXISTS idx_memory_item_category ON memory_item(category_id);
CREATE INDEX IF NOT EXISTS idx_memory_item_type ON memory_item(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_item_active ON memory_item(is_active);
