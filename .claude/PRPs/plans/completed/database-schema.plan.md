# Plan: Database Schema (schema.sql)

## Summary

Create `schema.sql` — a single, idempotent Postgres DDL file that defines all tables, indexes, RLS policies, and seed data for the competitive intelligence pipeline. The file must be safe to re-run against an existing Supabase project without errors. No application code is touched in this plan.

## User Story

As the GitHub Actions runner (and any developer setting up a fresh Supabase project),
I want a single `psql < schema.sql` command to fully provision the database,
So that scraper.py, analyzer.py, and the browser dashboard all work on first run without manual Supabase UI steps.

## Problem → Solution

No schema.sql exists → A fully idempotent DDL file that creates tables, indexes, RLS policies, and seeds competitor names in the correct dependency order.

## Metadata

- **Complexity**: Small
- **Source PRD**: `.claude/PRPs/prds/database-schema.prd.md`
- **PRD Phase**: All 4 phases (Tables → Indexes → RLS → Seed) — delivered as one file
- **Estimated Files**: 1 (schema.sql)

---

## UX Design

N/A — internal change. No user-facing UI is modified.

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `CLAUDE.md` | 140–165 | Authoritative column types, index list, RLS rules |
| P0 | `CLAUDE.md` | 22–26 | Competitor list (seed data, all 18 names) |
| P1 | `.claude/PRPs/prds/database-schema.prd.md` | all | Decisions log, query patterns, RLS rationale |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Supabase RLS | Documentation-Supabase.md | anon role = `anon`; service role bypasses RLS automatically; no service-role policy needed |
| `CREATE POLICY IF NOT EXISTS` | Postgres docs | **Does NOT exist** — use `DROP POLICY IF EXISTS … ON …; CREATE POLICY …` for idempotency |
| `CREATE INDEX IF NOT EXISTS` | Postgres docs | Supported since PG 9.5 — use freely |
| `CREATE TABLE IF NOT EXISTS` | Postgres docs | Supported — use freely |
| `gen_random_uuid()` | pgcrypto | Enabled by default on Supabase; guard with `CREATE EXTENSION IF NOT EXISTS pgcrypto` |

---

## Patterns to Mirror

### IDEMPOTENCY_TABLES
```sql
-- Use IF NOT EXISTS on every CREATE
CREATE TABLE IF NOT EXISTS raw_mentions (
  ...
);
```

### IDEMPOTENCY_INDEXES
```sql
-- IF NOT EXISTS supported for indexes
CREATE INDEX IF NOT EXISTS idx_mention_analyses_competitor_feature
  ON mention_analyses(competitor, feature);
```

### IDEMPOTENCY_POLICIES
```sql
-- CREATE POLICY has NO IF NOT EXISTS — drop first, then create
DROP POLICY IF EXISTS "anon_select" ON raw_mentions;
CREATE POLICY "anon_select" ON raw_mentions
  FOR SELECT TO anon USING (true);
```

### IDEMPOTENCY_SEED
```sql
-- INSERT … ON CONFLICT DO NOTHING for seed data
INSERT INTO search_terms (term, active, added_by)
VALUES ('Toggl', true, 'system')
ON CONFLICT (term) DO NOTHING;
```

### RLS_ENABLE
```sql
-- Must ALTER TABLE before CREATE POLICY
ALTER TABLE raw_mentions ENABLE ROW LEVEL SECURITY;
```

### FK_CONSTRAINT
```sql
-- mention_id references raw_mentions(id) with cascade delete
mention_id text REFERENCES raw_mentions(id) ON DELETE CASCADE,
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `schema.sql` | CREATE | Does not exist; this plan creates it |
| `.claude/PRPs/prds/database-schema.prd.md` | UPDATE | Mark all phases `in-progress` → `complete` after implementation |

## NOT Building

- scraper.py, analyzer.py, index.html — application code, separate PRDs
- Row-level auth (per-user RLS) — tool is public read-only
- Partitioning or archival tables
- Full-text search indexes on `body`
- Supabase migrations folder (`supabase/migrations/`) — out of scope; single flat file only

---

## Step-by-Step Tasks

### Task 1: Extension guard
- **ACTION**: Add `CREATE EXTENSION IF NOT EXISTS pgcrypto;` as first statement.
- **IMPLEMENT**: Single line; ensures `gen_random_uuid()` is available even on fresh projects where pgcrypto isn't pre-enabled.
- **MIRROR**: IDEMPOTENCY_TABLES (same IF NOT EXISTS guard pattern)
- **GOTCHA**: Supabase enables pgcrypto by default but the guard costs nothing and prevents confusing errors on vanilla Postgres.
- **VALIDATE**: File applies without error on a clean Postgres 15 instance.

### Task 2: Create `raw_mentions` table
- **ACTION**: Write `CREATE TABLE IF NOT EXISTS raw_mentions`.
- **IMPLEMENT**:
  ```sql
  CREATE TABLE IF NOT EXISTS raw_mentions (
    id                 text        PRIMARY KEY,
    subreddit          text,
    type               text,
    body               text,
    score              int,
    url                text,
    reddit_created_at  timestamptz,
    scraped_at         timestamptz DEFAULT now()
  );
  ```
- **MIRROR**: IDEMPOTENCY_TABLES
- **GOTCHA**: `id` is the Reddit fullname (`t3_abc123`), NOT a UUID. Do not add `DEFAULT gen_random_uuid()`.
- **VALIDATE**: `\d raw_mentions` in psql shows all 8 columns with correct types.

### Task 3: Create `mention_analyses` table
- **ACTION**: Write `CREATE TABLE IF NOT EXISTS mention_analyses`.
- **IMPLEMENT**:
  ```sql
  CREATE TABLE IF NOT EXISTS mention_analyses (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    mention_id       text        REFERENCES raw_mentions(id) ON DELETE CASCADE,
    competitor       text,
    feature          text,
    sentiment_score  numeric(3,2),
    sentiment_label  text,
    supporting_quote text,
    analyzed_at      timestamptz DEFAULT now()
  );
  ```
- **MIRROR**: FK_CONSTRAINT
- **GOTCHA**: `sentiment_score` is `numeric(3,2)` — values from 0.00 to 9.99. The RoBERTa model returns 0.0–1.0 so this is safe. Do not use `float` (rounding surprises in AVG aggregates).
- **VALIDATE**: `\d mention_analyses` shows FK to raw_mentions.

### Task 4: Create `search_terms` table
- **ACTION**: Write `CREATE TABLE IF NOT EXISTS search_terms`.
- **IMPLEMENT**:
  ```sql
  CREATE TABLE IF NOT EXISTS search_terms (
    id        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    term      text        UNIQUE NOT NULL,
    active    boolean     DEFAULT true,
    added_at  timestamptz DEFAULT now(),
    added_by  text        DEFAULT 'system'
  );
  ```
- **MIRROR**: IDEMPOTENCY_TABLES
- **GOTCHA**: `term` must have the `UNIQUE` constraint — it is the `on_conflict` key for both Python upserts (`supabase.table("search_terms").upsert(row, on_conflict="term")`) and JS upserts (`{ onConflict: 'term' }`).
- **VALIDATE**: `\d search_terms` shows `term text UNIQUE NOT NULL`.

### Task 5: Create all indexes
- **ACTION**: Add 5 indexes using `CREATE INDEX IF NOT EXISTS`.
- **IMPLEMENT**:
  ```sql
  -- Heatmap GROUP BY + pain-points filter + opportunity matrix
  CREATE INDEX IF NOT EXISTS idx_mention_analyses_competitor_feature
    ON mention_analyses(competitor, feature);

  -- Heatmap date-range filter
  CREATE INDEX IF NOT EXISTS idx_mention_analyses_analyzed_at
    ON mention_analyses(analyzed_at);

  -- JOIN to raw_mentions for subreddit filter (heatmap)
  CREATE INDEX IF NOT EXISTS idx_mention_analyses_mention_id
    ON mention_analyses(mention_id);

  -- Scraper dedup / time-based queries
  CREATE INDEX IF NOT EXISTS idx_raw_mentions_scraped_at
    ON raw_mentions(scraped_at);

  -- Heatmap subreddit filter on join side
  CREATE INDEX IF NOT EXISTS idx_raw_mentions_subreddit
    ON raw_mentions(subreddit);
  ```
- **MIRROR**: IDEMPOTENCY_INDEXES
- **GOTCHA**: `mention_analyses(mention_id)` and `raw_mentions(subreddit)` are NOT in the original CLAUDE.md index list but are required for the heatmap JOIN query. Both are now in CLAUDE.md (updated in prior session). Do not omit them.
- **VALIDATE**: `\di mention_analyses` lists all 4 indexes. Heatmap EXPLAIN shows `Index Scan` not `Seq Scan`.

### Task 6: Enable RLS on all tables
- **ACTION**: `ALTER TABLE … ENABLE ROW LEVEL SECURITY` on all three tables.
- **IMPLEMENT**:
  ```sql
  ALTER TABLE raw_mentions      ENABLE ROW LEVEL SECURITY;
  ALTER TABLE mention_analyses  ENABLE ROW LEVEL SECURITY;
  ALTER TABLE search_terms      ENABLE ROW LEVEL SECURITY;
  ```
- **MIRROR**: RLS_ENABLE
- **GOTCHA**: `ALTER TABLE … ENABLE ROW LEVEL SECURITY` is idempotent — safe to run multiple times (no-op if already enabled). No `IF NOT EXISTS` needed here.
- **VALIDATE**: `SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public'` shows `t` for all three.

### Task 7: RLS policies — `raw_mentions`
- **ACTION**: Drop-then-create anon SELECT policy. No other policies (writes blocked by absence of policy).
- **IMPLEMENT**:
  ```sql
  DROP POLICY IF EXISTS "anon_select_raw_mentions" ON raw_mentions;
  CREATE POLICY "anon_select_raw_mentions" ON raw_mentions
    FOR SELECT TO anon USING (true);
  ```
- **MIRROR**: IDEMPOTENCY_POLICIES
- **GOTCHA**: With RLS enabled and NO write policy for anon, anon INSERT/UPDATE/DELETE return `42501` (insufficient privilege). Service role bypasses RLS entirely — no service-role policy needed.
- **VALIDATE**: Using anon key: `SELECT count(*) FROM raw_mentions` → 200. `INSERT INTO raw_mentions …` → error 42501.

### Task 8: RLS policies — `mention_analyses`
- **ACTION**: Drop-then-create anon SELECT policy only.
- **IMPLEMENT**:
  ```sql
  DROP POLICY IF EXISTS "anon_select_mention_analyses" ON mention_analyses;
  CREATE POLICY "anon_select_mention_analyses" ON mention_analyses
    FOR SELECT TO anon USING (true);
  ```
- **MIRROR**: IDEMPOTENCY_POLICIES
- **GOTCHA**: Same as Task 7 — no write policy means anon writes are blocked. Python analyzer uses service key → bypasses RLS.
- **VALIDATE**: Anon SELECT succeeds; anon INSERT fails.

### Task 9: RLS policies — `search_terms`
- **ACTION**: Drop-then-create SELECT, INSERT, and UPDATE policies for anon. No DELETE policy.
- **IMPLEMENT**:
  ```sql
  DROP POLICY IF EXISTS "anon_select_search_terms" ON search_terms;
  CREATE POLICY "anon_select_search_terms" ON search_terms
    FOR SELECT TO anon USING (true);

  DROP POLICY IF EXISTS "anon_insert_search_terms" ON search_terms;
  CREATE POLICY "anon_insert_search_terms" ON search_terms
    FOR INSERT TO anon WITH CHECK (true);

  DROP POLICY IF EXISTS "anon_update_search_terms" ON search_terms;
  CREATE POLICY "anon_update_search_terms" ON search_terms
    FOR UPDATE TO anon USING (true) WITH CHECK (true);
  ```
- **MIRROR**: IDEMPOTENCY_POLICIES
- **GOTCHA**: INSERT policies use `WITH CHECK`, not `USING`. UPDATE needs both `USING` (which rows can be targeted) and `WITH CHECK` (what the row can become). Missing either clause causes a syntax error.
- **GOTCHA**: No DELETE policy for anon — intentional. Accidental browser-side delete of all terms would disable the scraper.
- **VALIDATE**: Anon key: INSERT new term → 201. UPDATE active flag → 200. DELETE → error 42501.

### Task 10: Seed `search_terms`
- **ACTION**: Insert all 18 competitor names with `ON CONFLICT DO NOTHING`.
- **IMPLEMENT**:
  ```sql
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
  ```
- **MIRROR**: IDEMPOTENCY_SEED
- **GOTCHA**: `ON CONFLICT (term) DO NOTHING` requires the exact column name that has the UNIQUE constraint — `term`, not `id`. If the constraint name differs, use `ON CONFLICT ON CONSTRAINT search_terms_term_key DO NOTHING`.
- **GOTCHA**: "Toggl Track" and "Toggl" are separate competitors — both must be present. Toggl is the parent brand; Toggl Track is the specific product.
- **VALIDATE**: `SELECT count(*) FROM search_terms WHERE active = true` → 18.

---

## Final File Structure

The completed `schema.sql` must follow this top-to-bottom order to satisfy Postgres dependency rules:

```
1. CREATE EXTENSION IF NOT EXISTS pgcrypto
2. CREATE TABLE IF NOT EXISTS raw_mentions
3. CREATE TABLE IF NOT EXISTS mention_analyses  (FK → raw_mentions)
4. CREATE TABLE IF NOT EXISTS search_terms
5. CREATE INDEX IF NOT EXISTS … (×5)
6. ALTER TABLE … ENABLE ROW LEVEL SECURITY (×3)
7. DROP POLICY IF EXISTS / CREATE POLICY … (×6 policies across 3 tables)
8. INSERT INTO search_terms … ON CONFLICT DO NOTHING
```

---

## Testing Strategy

### Manual Validation Sequence

| Step | Command | Expected |
|---|---|---|
| Apply schema | `psql $DATABASE_URL < schema.sql` | Zero errors, no notices |
| Re-apply (idempotency) | `psql $DATABASE_URL < schema.sql` | Zero errors again |
| Row count | `SELECT count(*) FROM search_terms;` | 18 |
| RLS enabled | `SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname='public';` | `t` for all 3 |
| Policy list | `SELECT * FROM pg_policies WHERE schemaname='public';` | 6 policies |
| Index list | `\di mention_analyses` | 4 indexes listed |
| Heatmap EXPLAIN | See below | Index scans |
| Anon SELECT | JS: `client.from('raw_mentions').select('*').limit(1)` | `data: [...]` |
| Anon INSERT blocked | JS: `client.from('raw_mentions').insert({...})` | `error.code: '42501'` |
| Anon INSERT search_terms | JS: `client.from('search_terms').insert({term:'test'})` | `data: [...]` |
| Anon DELETE search_terms blocked | JS: `client.from('search_terms').delete().eq('term','test')` | `error.code: '42501'` |

### Heatmap EXPLAIN Command
```sql
EXPLAIN SELECT ma.competitor, ma.feature, AVG(ma.sentiment_score)
FROM mention_analyses ma
JOIN raw_mentions rm ON ma.mention_id = rm.id
WHERE ma.analyzed_at BETWEEN '2026-01-01' AND '2026-12-31'
  AND rm.subreddit = 'freelance'
GROUP BY ma.competitor, ma.feature;
```
EXPECT: `Index Scan using idx_mention_analyses_analyzed_at` (or similar index scan node), not `Seq Scan on mention_analyses`.

Note: On an empty table Postgres may choose a seq scan regardless — this is expected. Test EXPLAIN on a populated table or use `SET enable_seqscan = off` to force index usage for plan verification.

### Edge Cases Checklist
- [ ] Re-apply schema on a database where tables already exist → no errors
- [ ] Re-apply when seed data already exists → no duplicate rows (ON CONFLICT DO NOTHING)
- [ ] Re-apply when RLS already enabled → `ALTER TABLE … ENABLE RLS` is no-op
- [ ] Re-apply when policies already exist → DROP IF EXISTS clears old policy, CREATE recreates

---

## Validation Commands

### Apply Schema
```bash
psql "$DATABASE_URL" < schema.sql
```
EXPECT: Zero errors. If using Supabase CLI: `supabase db push` (but direct psql is simpler for this one-file schema).

### Re-apply (Idempotency Check)
```bash
psql "$DATABASE_URL" < schema.sql && echo "IDEMPOTENT OK"
```
EXPECT: `IDEMPOTENT OK` with no errors on second run.

### Verify Seed Count
```bash
psql "$DATABASE_URL" -c "SELECT count(*) FROM search_terms WHERE active = true;"
```
EXPECT: `18`

### Verify Policies
```bash
psql "$DATABASE_URL" -c "SELECT tablename, policyname, cmd, roles FROM pg_policies WHERE schemaname = 'public' ORDER BY tablename, cmd;"
```
EXPECT: 6 rows — SELECT×3 tables, INSERT×1 (search_terms), UPDATE×1 (search_terms).

---

## Acceptance Criteria
- [ ] `schema.sql` exists at project root
- [ ] File applies with zero errors (`psql < schema.sql`)
- [ ] File re-applies with zero errors (idempotency)
- [ ] All 3 tables created with correct column types
- [ ] All 5 indexes created
- [ ] RLS enabled on all 3 tables
- [ ] 6 RLS policies present (SELECT×3, INSERT×1, UPDATE×1 — no anon DELETE anywhere)
- [ ] `SELECT count(*) FROM search_terms` returns 18
- [ ] Anon SELECT works on all tables
- [ ] Anon INSERT/UPDATE blocked on raw_mentions and mention_analyses
- [ ] Anon INSERT/UPDATE works on search_terms
- [ ] Anon DELETE blocked on search_terms

## Completion Checklist
- [ ] Column types match CLAUDE.md exactly
- [ ] Both Toggl and Toggl Track present in seed data
- [ ] `mention_id` FK uses ON DELETE CASCADE
- [ ] No UUID default on raw_mentions.id
- [ ] Policy names are consistent and descriptive
- [ ] File order respects Postgres dependency rules (FK table created before referencing table)
- [ ] No hardcoded connection strings or credentials

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `CREATE POLICY IF NOT EXISTS` used accidentally | Low | Error on apply | Use DROP POLICY IF EXISTS pattern (documented in plan) |
| Seed insert fails due to constraint name mismatch | Low | Partial seed | Use `ON CONFLICT (term)` not constraint name |
| Heatmap slow on empty table (seq scan in EXPLAIN) | High | False alarm | Note in validation: force index with `SET enable_seqscan = off` |
| Anon DELETE on search_terms accidentally allowed | Low | Scraper disabled | No DELETE policy created — verified by policy count check (6, not 7) |

## Notes

- The file is a plain `.sql` script — no Supabase migration wrapper needed. It can also be pasted directly into the Supabase SQL Editor.
- `numeric(3,2)` for `sentiment_score` accommodates values 0.00–9.99. The RoBERTa model outputs 0.0–1.0, so precision is safe. Using `numeric` (not `float8`) avoids floating-point rounding drift in `AVG()` aggregates across many rows.
- "Toggl" and "Toggl Track" are intentionally both seeded — they appear as distinct competitors in the analysis pipeline.
