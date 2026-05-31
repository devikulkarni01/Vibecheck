# Database Schema — Competitive Intelligence Tool

## Problem Statement

The competitive intelligence pipeline (scraper.py → analyzer.py → index.html) requires a Postgres schema on Supabase that is safe to read from a public browser dashboard (anon key, RLS enforced) while keeping all writes gated behind the service role key used by the Python scripts. Without a correct schema, indexes, and RLS setup, the pipeline either fails to store data, exposes write endpoints to the browser, or runs heatmap queries without the indexes needed for acceptable latency.

## Evidence

- CLAUDE.md specifies all four tables, their columns, and the exact index list required.
- The heatmap tab performs a JOIN between `mention_analyses` and `raw_mentions` to apply the subreddit filter, which requires a separate index not listed in the current CLAUDE.md index list.
- The JS dashboard uses the Supabase anon key — any missing or incorrect RLS policy would either expose writes or break reads.
- CLAUDE.md explicitly notes: "RLS: anonymous SELECT on all tables. Anonymous INSERT/UPDATE on search_terms only."

## Proposed Solution

A single `schema.sql` file that:
1. Creates the three tables with exact column types from CLAUDE.md.
2. Enables RLS on every table and adds the minimal policy set (anon SELECT on all; anon INSERT/UPDATE on `search_terms` only; all other writes implicitly blocked for anon, pass-through for service role which bypasses RLS).
3. Adds all indexes needed for the heatmap, pain-points, and opportunity-matrix queries.
4. Seeds `search_terms` with all 18 competitor names.

## Key Hypothesis

We believe a correct, indexed schema with tight RLS will unblock all downstream pipeline components (scraper, analyzer, dashboard) without requiring any application-level auth logic.
We'll know we're right when the dashboard heatmap query returns results in <2 s on the Supabase free tier with a realistic dataset.

## What We're NOT Building

- **Application logic** — scraper.py, analyzer.py, index.html are out of scope for this PRD.
- **Authentication / user accounts** — the tool is read-only public; no auth rows-per-user RLS.
- **Partitioning or archival** — data volume on free tier does not justify it.
- **Full-text search indexes** — body text search is handled by Reddit API, not Postgres.

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| Schema applies cleanly | Zero errors on `psql < schema.sql` | CI run |
| Anon SELECT works | 200 from JS dashboard on all tables | Manual test with anon key |
| Anon write blocked | 403/RLS error on INSERT to raw_mentions via anon key | Manual test |
| Anon write allowed on search_terms | 200 on INSERT/UPDATE via anon key | Manual test |
| Heatmap query plan uses indexes | `EXPLAIN` shows index scans, not seq scans | EXPLAIN output |

## Open Questions

- [ ] Should `raw_mentions.subreddit` be constrained to the known subreddit list, or left as free text for forward-compatibility?
- [ ] Should `mention_analyses.mention_id` have ON DELETE CASCADE so orphaned analyses are cleaned up if a raw mention is deleted?

---

## Users & Context

**Primary User**
- **Who**: The GitHub Actions runner (service role) and the browser dashboard (anon role).
- **Current behavior**: No schema exists; pipeline cannot run.
- **Trigger**: First deploy of the pipeline.
- **Success state**: `scraper.py` upserts without error; dashboard renders heatmap.

**Job to Be Done**
When the pipeline runs for the first time, the schema must already exist so that all three components (scraper, analyzer, dashboard) can operate without code changes.

**Non-Users**
End-users of the dashboard are not Postgres users — they interact only through the Supabase JS client with the anon key. No Postgres-level user management is needed.

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | `raw_mentions` table | Stores Reddit posts and comments |
| Must | `mention_analyses` table | Stores Haiku + RoBERTa output |
| Must | `search_terms` table | Runtime-configurable scraper keywords |
| Must | RLS: anon SELECT on all tables | Dashboard uses anon key |
| Must | RLS: block anon writes on raw_mentions and mention_analyses | Service-role-only ingestion |
| Must | RLS: allow anon INSERT/UPDATE on search_terms | Keyword manager tab in dashboard |
| Must | Index on (competitor, feature) | Heatmap GROUP BY |
| Must | Index on analyzed_at | Heatmap date-range filter |
| Must | Index on scraped_at | Scraper deduplication queries |
| Must | Index on mention_analyses(mention_id) | JOIN to raw_mentions for subreddit filter |
| Must | Index on raw_mentions(subreddit) | Heatmap subreddit filter |
| Must | Seed search_terms with 18 competitor names | Scraper reads terms at runtime |
| Should | `ON DELETE CASCADE` on mention_analyses.mention_id FK | Avoid orphaned rows |
| Won't | Partitioning | Free tier; data volume does not justify it |
| Won't | FTS indexes | Not needed; search is done at Reddit API level |

### MVP Scope

All Must items above. The schema must be idempotent (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `CREATE POLICY IF NOT EXISTS`) so it can be re-applied safely.

### Query Patterns Supported

**Heatmap** (critical path):
```sql
SELECT ma.competitor, ma.feature, AVG(ma.sentiment_score)
FROM mention_analyses ma
JOIN raw_mentions rm ON ma.mention_id = rm.id
WHERE ma.analyzed_at BETWEEN :start AND :end
  AND (:subreddit IS NULL OR rm.subreddit = :subreddit)
GROUP BY ma.competitor, ma.feature;
```
Indexes used: `mention_analyses(analyzed_at)`, `mention_analyses(mention_id)`, `raw_mentions(subreddit)`, `mention_analyses(competitor, feature)`.

**Pain points** (paginated):
```sql
SELECT * FROM mention_analyses
WHERE competitor = :c AND feature = :f
ORDER BY sentiment_score ASC
LIMIT 50 OFFSET :offset;
```
Index used: `mention_analyses(competitor, feature)`.

**Opportunity matrix**:
```sql
SELECT feature, competitor, AVG(sentiment_score) AS avg_score
FROM mention_analyses
GROUP BY feature, competitor
HAVING AVG(sentiment_score) < :threshold;
```
Index used: `mention_analyses(competitor, feature)`.

---

## Technical Approach

**Feasibility**: HIGH — standard Postgres DDL; no extensions needed beyond `pgcrypto` (already enabled on Supabase for `gen_random_uuid()`).

**Architecture Notes**
- `raw_mentions.id` is the Reddit fullname (e.g. `t3_abc123`) — text PK, not UUID, to enable natural upsert deduplication.
- `mention_analyses.id` is UUID to avoid exposing Reddit IDs in analysis rows.
- RLS uses `TO anon` role for policies; service role bypasses RLS automatically on Supabase.
- `search_terms` allows anon INSERT/UPDATE so the Keyword Manager tab in the browser can add/toggle terms. Deletes remain service-role-only to prevent accidental removal of all terms.

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `gen_random_uuid()` not available | Low | Supabase enables pgcrypto by default; add explicit `CREATE EXTENSION IF NOT EXISTS pgcrypto` |
| RLS policy gap allows anon DELETE on search_terms | Medium | Only grant INSERT and UPDATE, not DELETE, to anon on search_terms |
| Heatmap query slow without subreddit index | Medium | Add index on `raw_mentions(subreddit)` — identified as missing from CLAUDE.md list |
| Schema re-apply fails if objects exist | Low | Use `IF NOT EXISTS` on all DDL |

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | Tables | CREATE TABLE statements for all three tables | complete | - | - | `.claude/PRPs/plans/completed/database-schema.plan.md` |
| 2 | Indexes | All indexes for heatmap, pain points, opportunity matrix, and FK join | complete | with 1 | - | `.claude/PRPs/plans/completed/database-schema.plan.md` |
| 3 | RLS policies | Enable RLS + anon SELECT on all + anon INSERT/UPDATE on search_terms | complete | - | 1 | `.claude/PRPs/plans/completed/database-schema.plan.md` |
| 4 | Seed data | INSERT competitor names into search_terms | complete | - | 1, 3 | `.claude/PRPs/plans/completed/database-schema.plan.md` |

### Phase Details

**Phase 1: Tables**
- **Goal**: Define all columns with correct types, constraints, and defaults.
- **Scope**: `raw_mentions`, `mention_analyses`, `search_terms` CREATE TABLE statements.
- **Success signal**: Tables visible in Supabase Table Editor with correct schema.

**Phase 2: Indexes**
- **Goal**: All query patterns use index scans, not sequential scans.
- **Scope**: 5 indexes: `(competitor, feature)`, `(analyzed_at)`, `(scraped_at)`, `(mention_id)`, raw_mentions `(subreddit)`.
- **Success signal**: `EXPLAIN` on heatmap query shows index scans.

**Phase 3: RLS Policies**
- **Goal**: Browser dashboard can read but not write raw data; can write search_terms.
- **Scope**: `ALTER TABLE … ENABLE ROW LEVEL SECURITY` + policy DDL for all three tables.
- **Success signal**: Anon key SELECT returns data; anon INSERT to raw_mentions returns 403.

**Phase 4: Seed Data**
- **Goal**: Scraper finds terms in search_terms on first run without manual setup.
- **Scope**: INSERT 18 competitor names into search_terms with `active = true`.
- **Success signal**: `SELECT count(*) FROM search_terms` returns 18.

### Parallelism Notes

Phases 1 and 2 can run in parallel in the SQL file (indexes after table creation in the same transaction). Phase 3 depends on tables existing. Phase 4 depends on tables and RLS existing.

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| raw_mentions PK type | `text` (Reddit fullname) | UUID | Natural dedup key; avoids separate UNIQUE constraint |
| mention_analyses PK type | UUID | text | No natural key; UUID is idiomatic for Supabase |
| Anon delete on search_terms | Blocked | Allowed | Accidental delete of all terms would disable scraper until manual fix |
| Index on mention_id | Added | Omitted | Required for JOIN performance on heatmap subreddit filter |
| Index on raw_mentions(subreddit) | Added | Omitted | Required for heatmap subreddit filter; not in CLAUDE.md but needed |
| Idempotent DDL | `IF NOT EXISTS` everywhere | Plain DDL | Safe to re-apply after partial failures |

---

## Research Summary

**Schema Context**
All table definitions, column types, and the base index list are specified in CLAUDE.md. Two indexes not in the CLAUDE.md list are required by the heatmap query: `mention_analyses(mention_id)` (FK join) and `raw_mentions(subreddit)` (join-side filter). These are added in this PRD.

**RLS Context**
CLAUDE.md states "Anonymous SELECT on all tables. Anonymous INSERT/UPDATE on search_terms only." Anon DELETE on search_terms is implicitly blocked (no policy grants it). Service role bypasses RLS on Supabase by design — no explicit service-role policy needed.

**Seed Data**
CLAUDE.md: "Seed search_terms with all competitor names." Competitors list: Rize, Timely, Reclaim.ai, TimeCamp, Memtime, Timeular, Clockk, Hubstaff, Toggl Track, Toggl, Clockify, Harvest, RescueTime, Carly AI, Replicon, ZeroTime, Kickidler, Flowace (18 entries).

---

*Generated: 2026-05-30*
*Status: DRAFT — ready for /prp-plan*
