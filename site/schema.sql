-- Chitragupta waitlist — D1 schema
-- Run: wrangler d1 execute chitragupta-waitlist --file=schema.sql

CREATE TABLE IF NOT EXISTS waitlist (
  email      TEXT    NOT NULL UNIQUE,
  name       TEXT    NOT NULL,
  linkedin   TEXT,
  use_case   TEXT,
  ip         TEXT,
  created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Index on email for fast idempotent checks
CREATE INDEX IF NOT EXISTS idx_waitlist_email ON waitlist(email);
