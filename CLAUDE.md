# Competitive Intelligence Tool — Freelancer Time Tracking Market

## Stack
- Python 3.11+, supabase-py, anthropic, transformers, torch (CPU)
- Supabase free tier (Postgres)
- claude-haiku-4-5 only
- cardiffnlp/twitter-roberta-base-topic-sentiment-latest (local inference)
- Reddit unauthenticated JSON API (no PRAW)
- Dashboard: index.html — vanilla JS + Supabase JS v2 CDN, no build step

## Files
```
scraper.py
analyzer.py
index.html
schema.sql
requirements.txt
.env.example
.github/workflows/run.yml
```

## Competitors
Rize, Timely, Reclaim.ai, TimeCamp, Memtime, Timeular, Clockk, Hubstaff,
Toggl Track, Toggl, Clockify, Harvest, RescueTime, Carly AI, Replicon,
ZeroTime, Kickidler, Flowace

## Subreddits
freelance, freelancedesign, consulting, smallbusiness, webdev, graphic_design,
productivityapps, workforcemanagement, Entrepreneur, productivity, timetracking,
timetrackingsoftware, remotework, dataisbeautiful, askreddit

## Features
automatic time tracking, manual time tracking, voice time tracking, voice capture,
speech to text, idle detection, offline tracking, timer accuracy, background tracking,
browser extension, desktop app, mobile app, GPS tracking,
invoicing, billing, payment processing, expense tracking, budget tracking,
AI features, smart scheduling, AI insights, AI reports, meeting detection, calendar AI,
employee monitoring, screenshots, team analytics, team dashboard, project budgeting,
payroll integration, GPS tracking, geofencing,
calendar sync, Jira integration, Asana integration, Slack integration,
QuickBooks integration, Xero integration, API access, data export, excel export,
Zapier integration, project tool integrations, accounting integrations,
ease of use, onboarding, customer support, UI design, pricing, free tier,
privacy concerns, surveillance concerns, accuracy, reliability

## Reddit scraping
```
GET https://www.reddit.com/r/{subreddit}/search.json
  ?q={term}&restrict_sr=true&limit=100&sort=relevance&t=all
User-Agent: CompetitiveIntelBot/1.0
Rate limit: time.sleep(1) between requests
Pagination: follow data.after up to 3 pages
```
Popularity thresholds (discard below, never upsert):
- POST_MIN_SCORE = 5, POST_MIN_COMMENTS = 2
- COMMENT_MIN_SCORE = 2

Read active search terms from search_terms table at runtime (not hardcoded).
Upsert to raw_mentions by Reddit ID. Retry on 429: exponential backoff 5s, max 3 retries.
Accept --dry-run flag.

## Analysis pipeline

**Layer 1 — Haiku (extraction only, no scoring)**
Batch 25 pre-filtered mentions. Pre-filter: string match ≥1 competitor name.
Skip mention_ids already in mention_analyses.
Retry once on malformed JSON, then skip and log.

System prompt:
```
You extract structured data from Reddit text about time-tracking tools.
Competitors: {COMPETITOR_LIST}
Features: {FEATURE_LIST}
Return ONLY a valid JSON array, no preamble, no fences.
Each element: {"mention_id":str,"competitor":str,"feature":str,"supporting_quote":str}
supporting_quote: ≤20 words. Return [] if no signal.
```

**Layer 2 — cardiffnlp model (deterministic scoring)**
Run on supporting_quote from Layer 1. Load pipeline once at startup, reuse across batches.
Truncate to 128 tokens before inference. Outputs: sentiment_label + sentiment_score (0.0–1.0).

## Schema

```sql
-- raw_mentions
id text PRIMARY KEY, subreddit text, type text, body text,
score int, url text, reddit_created_at timestamptz, scraped_at timestamptz DEFAULT now()

-- mention_analyses
id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
mention_id text REFERENCES raw_mentions(id),
competitor text, feature text,
sentiment_score numeric(3,2), sentiment_label text,
supporting_quote text, analyzed_at timestamptz DEFAULT now()

-- search_terms
id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
term text UNIQUE NOT NULL, active boolean DEFAULT true,
added_at timestamptz DEFAULT now(), added_by text DEFAULT 'system'

-- indexes
CREATE INDEX ON mention_analyses(competitor, feature);
CREATE INDEX ON mention_analyses(analyzed_at);
CREATE INDEX ON raw_mentions(scraped_at);
```

RLS: anonymous SELECT on all tables. Anonymous INSERT/UPDATE on search_terms only.
Python scripts use service role key. Seed search_terms with all competitor names.

## Dashboard (index.html)
Supabase URL + anon key as JS constants at top of file. Four tabs:

1. **Heatmap** — competitors × features, avg sentiment_score, red→yellow→green.
   Filters: date range, subreddit. Click cell → opens Pain Points tab filtered.
2. **Pain points** — paginated mentions for selected competitor + feature.
   Sort by sentiment_score asc or Reddit score.
3. **Opportunity matrix** — features where all competitors avg < threshold (slider, default 0.45).
4. **Keyword manager** — search_terms table, toggle active, add new terms.
   Note: changes apply on next scheduled run.

## Cost rules
1. String-match pre-filter before any API call
2. Skip already-analyzed mention_ids
3. Batch exactly 25 mentions per Haiku call
4. claude-haiku-4-5 only
5. Haiku extracts only — no sentiment scoring in prompt
6. Log token usage + estimated cost per batch
7. cardiffnlp pipeline loaded once per process

## GitHub Actions (.github/workflows/run.yml)
Cron: 06:00 UTC daily + workflow_dispatch.
Install torch CPU: `pip install torch --index-url https://download.pytorch.org/whl/cpu`
Cache ~/.cache/huggingface between runs.
Secrets: SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY
Runs: scraper.py → analyzer.py. No HTML commit step.

## Env vars
```
SUPABASE_URL          # Settings → API → Project URL
SUPABASE_KEY          # Settings → API → anon public  (safe in client JS with RLS)
SUPABASE_SERVICE_KEY  # Settings → API → service_role (Python only, never client-side)
ANTHROPIC_API_KEY     # console.anthropic.com → API Keys
```

## Everything Claude Code conventions
For every component: /documentation-lookup → /prp-prd → /prp-plan → /prp-implement → /code-review
Use --parallel on /prp-plan for large features.