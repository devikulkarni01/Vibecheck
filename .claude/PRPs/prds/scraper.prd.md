# scraper.py — Reddit Mention Scraper

## Problem Statement

The competitive intelligence tool needs fresh Reddit data to power its sentiment heatmap and pain-point dashboard. Without a scraper, the Supabase `raw_mentions` table is empty and all downstream analysis is blocked. The scraper must be keyword-manager-driven so non-technical users can add or pause search terms from the UI without touching code.

## Evidence

- Dashboard keyword manager tab already exists in the spec; it writes to `search_terms` — that table has no reader yet.
- Reddit's 2023 API policy enforces OAuth for all search requests; unauthenticated `*.json` endpoints return 403.
- GitHub Actions cron (06:00 UTC) is already defined in CLAUDE.md — scraper.py is the first step in that pipeline.

## Proposed Solution

A single Python script (`scraper.py`) that: (1) fetches an OAuth token from Reddit at startup, (2) reads active search terms from the Supabase `search_terms` table, (3) for each term × subreddit pair searches Reddit with up to 3 pages of pagination, (4) applies popularity thresholds, and (5) upserts qualifying posts/comments to `raw_mentions`. A `--dry-run` flag prints rows without writing.

## Key Hypothesis

We believe a daily Reddit scraper driven by the keyword manager will populate `raw_mentions` with enough signal (≥ 50 qualifying mentions per run) to make the sentiment heatmap meaningful within the first week of operation.
We'll know we're right when the heatmap shows non-empty cells for at least 10 competitor × feature pairs after 7 days.

## What We're NOT Building

- Comment thread crawling (only top-level posts and their `selftext`) — keeps scope tight for v1.
- PRAW or any Reddit OAuth library — plain `requests` only, per CLAUDE.md.
- Incremental / change-detection logic — full re-search per run; idempotency is handled by upsert-on-conflict.
- Proxy rotation or CAPTCHA bypass — rate limit is respected with `time.sleep(1)`.

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| Mentions upserted per daily run | ≥ 50 | `SELECT COUNT(*) FROM raw_mentions WHERE scraped_at >= now() - interval '1 day'` |
| 429 rate (Reddit throttle hits) | < 5% of requests | Logged per run |
| Duplicate-upsert conflicts | 0 errors | Upsert ON CONFLICT does not raise |
| Dry-run produces no DB writes | 100% | Manual spot-check |

## Open Questions

- [ ] Which Reddit account / username should appear in the User-Agent string? (`by /u/<username>`)
- [ ] Should comments be scraped (type=`t1`) in addition to posts (type=`t3`), or posts only for v1?

---

## Users & Context

**Primary User**
- **Who**: The GitHub Actions runner (automated) + the developer running manual test scrapes locally.
- **Current behavior**: No scraper exists; raw_mentions table is empty.
- **Trigger**: Cron fires at 06:00 UTC, or developer runs `python scraper.py [--dry-run]`.
- **Success state**: raw_mentions is populated; next step (`analyzer.py`) has data to process.

**Job to Be Done**
When the daily cron fires, I want to collect fresh Reddit mentions for every active search term, so I can keep the sentiment dashboard current without manual effort.

**Non-Users**
End-users of the dashboard — they interact via the keyword manager UI, not the scraper directly.

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | Fetch Reddit OAuth token at startup (client_credentials) | All search endpoints require a bearer token |
| Must | Read active search terms from `search_terms` Supabase table at runtime | Keyword manager must control what gets scraped |
| Must | Search all 15 configured subreddits for each active term | Coverage across relevant communities |
| Must | Paginate up to 3 pages per (term, subreddit) query | Caps request volume; Reddit returns 100/page → up to 300 candidates per pair |
| Must | Apply popularity thresholds before upsert (POST_MIN_SCORE=5, POST_MIN_COMMENTS=2; COMMENT_MIN_SCORE=2) | Filters noise |
| Must | Upsert to `raw_mentions` by Reddit fullname (`name` field) with ON CONFLICT on `id` | Idempotency |
| Must | Retry on HTTP 429 with exponential backoff (5s base, max 3 retries) | Reddit rate-limit resilience |
| Must | Accept `--dry-run` flag — print rows, no DB writes | Safe local testing |
| Should | Log summary at end: terms processed, requests made, rows upserted, 429 count | Operational visibility |
| Could | Per-subreddit progress logging | Debugging aid |
| Won't | Comment-level (`t1`) scraping | Posts + selftext sufficient for v1 |
| Won't | Incremental cursor persistence across runs | Upsert idempotency makes full re-scan safe |

### MVP Scope

OAuth token fetch → read search_terms → iterate (term × subreddit) → paginate 3 pages → threshold filter → upsert raw_mentions. Plus `--dry-run`.

### User Flow

```
startup
  └─ fetch Reddit OAuth token (POST /api/v1/access_token)
  └─ read active terms from search_terms (Supabase)
  └─ for each term:
       for each subreddit (15):
         page = 1..3 (stop early if data.after is null)
           GET /r/{sub}/search?q={term}&limit=100&after={cursor}
           sleep(1)
           for each child:
             apply threshold filter
             if passes → upsert raw_mentions
  └─ log summary
```

---

## Technical Approach

**Feasibility**: HIGH — all patterns specified in CLAUDE.md; no ambiguity.

**Architecture Notes**
- Single file, no classes. Functions: `get_reddit_token()`, `fetch_active_terms()`, `search_subreddit(token, subreddit, term, after)`, `passes_threshold(child_data)`, `build_row(child_data, subreddit)`, `upsert_row(supabase, row, dry_run)`, `main()`.
- Supabase client: `create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)` — service role, bypasses RLS.
- Reddit base URL for search: `https://oauth.reddit.com/r/{subreddit}/search`
- Token endpoint: `https://www.reddit.com/api/v1/access_token`
- User-Agent: `script:CompetitiveIntelBot:v1.0 (by /u/<username>)`
- Env vars required: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`.

**raw_mentions row shape**
```python
{
  "id": child["name"],           # e.g. "t3_abc123" — PRIMARY KEY
  "subreddit": subreddit,
  "type": child["kind"],         # "t3" (post) or "t1" (comment)
  "body": child["data"].get("selftext") or child["data"].get("body", ""),
  "score": child["data"]["score"],
  "url": "https://reddit.com" + child["data"]["permalink"],
  "reddit_created_at": datetime.utcfromtimestamp(child["data"]["created_utc"]).isoformat() + "Z",
}
```

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Reddit token expires mid-run (1h TTL) | L | Run completes well within 1h at 1 req/s with 15 subs × N terms |
| 429 burst despite sleep(1) | M | Exponential backoff, max 3 retries, then skip + log |
| search_terms table empty | L | Log warning and exit cleanly |
| Malformed response shape | L | Defensive `.get()` with defaults; log and skip on KeyError |

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | Auth & config | OAuth token fetch, env var loading, arg parsing | complete | - | - | `.claude/PRPs/plans/scraper.plan.md` |
| 2 | Data layer | Supabase client init, read search_terms, upsert helper | complete | with 1 | - | `.claude/PRPs/plans/scraper.plan.md` |
| 3 | Scrape loop | Subreddit search, pagination, threshold filter, row builder | complete | - | 1, 2 | `.claude/PRPs/plans/scraper.plan.md` |
| 4 | Resilience | 429 retry with backoff, per-run summary logging | complete | - | 3 | `.claude/PRPs/plans/scraper.plan.md` |
| 5 | Dry-run & smoke test | --dry-run flag wiring, manual end-to-end verification | complete | - | 4 | `.claire/PRPs/plans/scraper.plan.md` |

### Phase Details

**Phase 1: Auth & config**
- **Goal**: Reliably obtain a Reddit bearer token and load all required env vars.
- **Scope**: `get_reddit_token()`, `argparse` for `--dry-run`, env var validation with early exit on missing vars.
- **Success signal**: `python scraper.py --dry-run` prints token (first 10 chars) and exits without error.

**Phase 2: Data layer**
- **Goal**: Supabase read and write wired up correctly.
- **Scope**: `fetch_active_terms()` returns list of strings; `upsert_row()` calls `.upsert(row, on_conflict="id")`.
- **Success signal**: `fetch_active_terms()` returns at least the seeded competitor names; upsert does not raise on duplicate.

**Phase 3: Scrape loop**
- **Goal**: For every (term, subreddit) pair, collect up to 300 candidate rows and filter to qualifying ones.
- **Scope**: `search_subreddit()`, `passes_threshold()`, `build_row()`, outer nested loops in `main()`.
- **Success signal**: Dry-run prints ≥ 1 row for a known active term (e.g. "Toggl") in a high-traffic subreddit (e.g. `r/freelance`).

**Phase 4: Resilience**
- **Goal**: Handle 429s without crashing; surface a run summary.
- **Scope**: Retry decorator / inline retry loop; end-of-run `print(f"Terms: {n}, Requests: {r}, Upserted: {u}, 429s: {t429}")`.
- **Success signal**: Artificially throttled test (mock 429 response) retries and continues.

**Phase 5: Dry-run & smoke test**
- **Goal**: Confirm `--dry-run` writes nothing and a live run upserts rows.
- **Scope**: Flag logic; manual test against real credentials in GitHub Actions `workflow_dispatch`.
- **Success signal**: `SELECT COUNT(*) FROM raw_mentions` increases after a live run; `--dry-run` leaves count unchanged.

### Parallelism Notes

Phases 1 and 2 are independent (auth vs. DB layer) and can be written in parallel. Phase 3 depends on both. Phases 4 and 5 are sequential polish passes.

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| Reddit auth | OAuth client_credentials | Unauthenticated JSON | Reddit blocks unauthenticated requests (403) since 2023 |
| HTTP library | `requests` | `httpx`, `aiohttp` | Synchronous is simpler; rate-limit sleep(1) makes async irrelevant |
| Concurrency | None (sequential) | asyncio, ThreadPool | 60 req/min limit means parallelism gains nothing and complicates retry logic |
| Search endpoint | `/r/{sub}/search` per subreddit | `reddit.com/search` global | `restrict_sr=true` per sub gives cleaner, more targeted signal |
| Pagination cap | 3 pages | unlimited | Caps daily request count; ~300 candidates per pair is sufficient |
| Term source | `search_terms` Supabase table | Hardcoded list | UI keyword manager must control scrape scope |

---

## Research Summary

**Market Context**
Reddit is the primary community signal source for freelancer tool sentiment. All search requires OAuth since 2023. Rate limit is 60 req/min with valid credentials.

**Technical Context**
- OAuth token flow: `POST https://www.reddit.com/api/v1/access_token` with `grant_type=client_credentials` and Basic auth. Token TTL: 3600s.
- Search: `GET https://oauth.reddit.com/r/{sub}/search?q={term}&restrict_sr=true&limit=100&sort=relevance&t=all&after={cursor}`.
- Pagination cursor: `response["data"]["after"]`; `null` signals last page.
- Row ID: `child["name"]` (Reddit fullname, e.g. `t3_abc123`) — maps to `raw_mentions.id`.
- Supabase upsert pattern: `.upsert(row, on_conflict="id").execute()` — safe to re-run.

---

*Generated: 2026-05-30*
*Status: DRAFT — ready for /prp-plan*
