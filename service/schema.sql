-- Tables of the hosted service (worker.js), in its D1 database:
--   npx wrangler d1 execute reachy-meetings --remote --file schema.sql

CREATE TABLE IF NOT EXISTS usage_minutes (
  install TEXT NOT NULL,
  minute  TEXT NOT NULL,          -- UTC, to the minute: 2026-11-18T15:04
  PRIMARY KEY (install, minute)
);

-- Installations that may go past the free time. Entered by hand:
--   npx wrangler d1 execute reachy-meetings --remote --command "INSERT INTO plans (install, note) VALUES ('<id>', 'who and why')"
CREATE TABLE IF NOT EXISTS plans (
  install TEXT PRIMARY KEY,
  note    TEXT,
  since   TEXT NOT NULL DEFAULT (datetime('now'))
);
