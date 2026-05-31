-- Competitive Intelligence Tool — Database Schema
-- Idempotent: safe to re-run against an existing Supabase project.
-- Apply: psql "$DATABASE_URL" < schema.sql
-- Or paste directly into Supabase SQL Editor.

-- ─── Extensions ──────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ─── Tables ──────────────────────────────────────────────────────────────────

-- Stores Reddit posts and comments fetched by scraper.py.
-- id is the Reddit fullname (e.g. t3_abc123) — natural dedup key, not a UUID.
CREATE TABLE IF NOT EXISTS raw_mentions (
  id                 text        PRIMARY KEY,
  subreddit          text,
  type               text,         -- 'post' | 'comment'
  body               text,
  score              int,
  url                text,
  reddit_created_at  timestamptz,
  scraped_at         timestamptz   DEFAULT now()
);

-- Stores Haiku extraction + RoBERTa sentiment output from analyzer.py.
-- mention_id FK cascades so orphaned analyses are cleaned up automatically.
CREATE TABLE IF NOT EXISTS mention_analyses (
  id               uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  mention_id       text          REFERENCES raw_mentions(id) ON DELETE CASCADE,
  competitor       text,
  feature          text,
  sentiment_score  numeric(3,2), -- 0.00–1.00 from RoBERTa; numeric avoids AVG drift
  sentiment_label  text,         -- 'strongly negative' | 'negative' | 'negative or neutral' | 'positive' | 'strongly positive'
  supporting_quote text,
  analyzed_at      timestamptz   DEFAULT now()
);

-- Runtime-configurable scraper keywords. Anon can INSERT/UPDATE (Keyword Manager tab).
-- term UNIQUE is the on_conflict key for both Python and JS upserts.
CREATE TABLE IF NOT EXISTS search_terms (
  id        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  term      text        UNIQUE NOT NULL,
  active    boolean     DEFAULT true,
  added_at  timestamptz DEFAULT now(),
  added_by  text        DEFAULT 'system'
);

-- ─── Indexes ─────────────────────────────────────────────────────────────────

-- Heatmap GROUP BY, pain-points WHERE, opportunity matrix GROUP BY
CREATE INDEX IF NOT EXISTS idx_mention_analyses_competitor_feature
  ON mention_analyses(competitor, feature);

-- Heatmap date-range filter
CREATE INDEX IF NOT EXISTS idx_mention_analyses_analyzed_at
  ON mention_analyses(analyzed_at);

-- JOIN to raw_mentions for heatmap subreddit filter
CREATE INDEX IF NOT EXISTS idx_mention_analyses_mention_id
  ON mention_analyses(mention_id);

-- Scraper dedup and time-based queries
CREATE INDEX IF NOT EXISTS idx_raw_mentions_scraped_at
  ON raw_mentions(scraped_at);

-- Heatmap subreddit filter on the join side
CREATE INDEX IF NOT EXISTS idx_raw_mentions_subreddit
  ON raw_mentions(subreddit);

-- ─── Row Level Security ───────────────────────────────────────────────────────
-- Service role bypasses RLS automatically — no service-role policies needed.
-- Anon writes are blocked by the absence of a write policy (not by an explicit deny).

ALTER TABLE raw_mentions      ENABLE ROW LEVEL SECURITY;
ALTER TABLE mention_analyses  ENABLE ROW LEVEL SECURITY;
ALTER TABLE search_terms      ENABLE ROW LEVEL SECURITY;

-- raw_mentions: anon SELECT only
DROP POLICY IF EXISTS "anon_select_raw_mentions" ON raw_mentions;
CREATE POLICY "anon_select_raw_mentions" ON raw_mentions
  FOR SELECT TO anon USING (true);

-- mention_analyses: anon SELECT only
DROP POLICY IF EXISTS "anon_select_mention_analyses" ON mention_analyses;
CREATE POLICY "anon_select_mention_analyses" ON mention_analyses
  FOR SELECT TO anon USING (true);

-- search_terms: anon SELECT + INSERT + UPDATE (no DELETE — accidental delete disables scraper)
DROP POLICY IF EXISTS "anon_select_search_terms" ON search_terms;
CREATE POLICY "anon_select_search_terms" ON search_terms
  FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS "anon_insert_search_terms" ON search_terms;
CREATE POLICY "anon_insert_search_terms" ON search_terms
  FOR INSERT TO anon WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_search_terms" ON search_terms;
CREATE POLICY "anon_update_search_terms" ON search_terms
  FOR UPDATE TO anon USING (true) WITH CHECK (true);

-- ─── Seed Data ────────────────────────────────────────────────────────────────
-- 18 competitors from CLAUDE.md. ON CONFLICT DO NOTHING makes this idempotent.
-- "Toggl" and "Toggl Track" are distinct — Toggl Track is the specific product.

INSERT INTO search_terms (term, active, added_by) VALUES
  ('Rize',         true, 'system'),
  ('Timely',       true, 'system'),
  ('Reclaim.ai',   true, 'system'),
  ('TimeCamp',     true, 'system'),
  ('Memtime',      true, 'system'),
  ('Timeular',     true, 'system'),
  ('Clockk',       true, 'system'),
  ('Hubstaff',     true, 'system'),
  ('Toggl Track',  true, 'system'),
  ('Toggl',        true, 'system'),
  ('Clockify',     true, 'system'),
  ('Harvest',      true, 'system'),
  ('RescueTime',   true, 'system'),
  ('Carly AI',     true, 'system'),
  ('Replicon',     true, 'system'),
  ('ZeroTime',     true, 'system'),
  ('Kickidler',    true, 'system'),
  ('Flowace',      true, 'system')
ON CONFLICT (term) DO NOTHING;
