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

## Supabase client usage (see [Documentation-Supabase.md](Documentation-Supabase.md) for full reference)

**Python (`scraper.py`, `analyzer.py`) — service role key, bypasses RLS:**
```python
from supabase import create_client
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Upsert by Reddit ID (on_conflict must match UNIQUE constraint)
supabase.table("raw_mentions").upsert(row, on_conflict="id").execute()

# Filter query
supabase.table("mention_analyses").select("*").eq("competitor", "Toggl").lt("sentiment_score", 0.45).execute()
# response.data → list of dicts
```

**JS (`index.html`) — anon key, RLS enforced, CDN only, no build step:**
```html
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<script>
  const { createClient } = supabase
  const client = createClient(SUPABASE_URL, SUPABASE_KEY)  // anon key

  // Query with filters
  const { data, error } = await client.from('mention_analyses')
    .select('*').eq('competitor', 'Toggl').lt('sentiment_score', 0.45)
    .order('sentiment_score', { ascending: true }).range(0, 49)

  // Upsert (rows not returned by default — chain .select() if needed)
  await client.from('search_terms').upsert({ term: 'clockify' }, { onConflict: 'term' })
</script>
```

## Reddit scraping

> **OAuth required.** Unauthenticated `*.json` requests return 403 as of Reddit's 2023 API policy.
> Register a "script" app at reddit.com/prefs/apps and use client_credentials flow.
> See [Documentation-RedditAPI.md](Documentation-RedditAPI.md) for full reference.

```
# Token fetch at startup (expires in 1h)
POST https://www.reddit.com/api/v1/access_token
  grant_type=client_credentials
  Authorization: Basic base64(REDDIT_CLIENT_ID:REDDIT_CLIENT_SECRET)
  User-Agent: script:CompetitiveIntelBot:v1.0 (by /u/Real_Experience_3832)

# Search endpoint (use oauth.reddit.com, not www)
GET https://oauth.reddit.com/r/{subreddit}/search
  ?q={term}&restrict_sr=true&limit=100&sort=relevance&t=all&after={cursor}
  Authorization: bearer {token}
  User-Agent: script:CompetitiveIntelBot:v1.0 (by /u/Real_Experience_3832)
```

Response shape: `data.children[]` → each child is `{kind: "t3", data: {...}}` (posts) or `{kind: "t1", data: {...}}` (comments).
Pagination: `data.after` is the cursor for the next page (`null` = last page).
Rate limit: 60 req/min with OAuth. `time.sleep(1)` between pages is sufficient.

Scraping scope (v1 — manual runs only):
- Collects posts (`t3`) only — the `/r/{sub}/search` endpoint does not return comments
- Comment support (`t1`) is implemented in the code but requires a separate comment-thread fetch (future work)
- MAX_PAGES = 1 per (term × subreddit) pair — caps at 100 results per pair
- Total max requests per run: 18 terms × 15 subreddits × 1 page = 270 requests (~4.5 min at 1 req/s)
- Increase MAX_PAGES in a later release once volume is validated

Popularity thresholds (discard below, never upsert):
- POST_MIN_SCORE = 5, POST_MIN_COMMENTS = 2
- COMMENT_MIN_SCORE = 2

Read active search terms from search_terms table at runtime (not hardcoded).
String-match pre-filter: discard posts whose body contains no competitor name (case-insensitive). Applied before upsert.
Upsert to raw_mentions by Reddit ID (`name` field = fullname e.g. `t3_abc123`).
Retry on 429: exponential backoff 5s, max 3 retries.
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

Model details (see [Documentation-Roberta.md](Documentation-Roberta.md) for full reference):
- Load: `pipeline("text-classification", model="cardiffnlp/twitter-roberta-base-topic-sentiment-latest")`
- Call: `pipe(quote, truncation=True, max_length=128)` → `[{'label': str, 'score': float}]`
- Five labels: `strongly negative`, `negative`, `negative or neutral`, `positive`, `strongly positive`
- sentiment_label = `result[0]['label']`, sentiment_score = `result[0]['score']`
- No preprocessing needed — model handles @mentions, #hashtags, and URLs natively
- Hard token ceiling is 512; 128-token truncation is a safe conservative limit

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
CREATE INDEX ON mention_analyses(mention_id);  -- FK join to raw_mentions for subreddit filter
CREATE INDEX ON raw_mentions(scraped_at);
CREATE INDEX ON raw_mentions(subreddit);  -- heatmap subreddit filter (join side)
```

RLS: anonymous SELECT on all tables. Anonymous INSERT/UPDATE on search_terms only.
Python scripts use service role key. Seed search_terms with all competitor names.

## Dashboard (index.html)
Supabase URL + anon key as JS constants at top of file. Three tabs (v1):

1. **Heatmap** — competitors × features, avg sentiment_score, red→yellow→green.
   Filters: subreddit. Summary columns (total mentions + avg score) per competitor row.
   Empty rows/columns auto-deselected on load. Click cell → opens inline Pain Points drawer below heatmap.
2. **Pain points** — inline drawer below heatmap. Paginated mentions, sort by sentiment or Reddit score.
   Supporting quotes shown inline per row.
3. **Opportunity matrix** — two categories:
   - Features where all competitors avg < threshold (slider, default 0.45) → weak sentiment gap
   - Features with zero mentions across all competitors → unexplored market (shown with "no data" badge)

## v2 Roadmap (do not build until v1 is validated)
- **Keyword Manager tab** — search_terms table CRUD (anon key already supports INSERT/UPDATE on search_terms)
- **localStorage threshold** — persist opportunity threshold slider value across sessions
- **Historical trend view** — time-series chart of sentiment per competitor/feature over time
- **GitHub Actions trigger button** — "Run Collection" button calling `workflow_dispatch` API; show run status
- **Add cron schedule** (`0 6 * * *`) after validating manual run volume and cost; add `~/.cache/huggingface` cache step so cardiffnlp model is not re-downloaded on every run
- **Mobile responsive layout** — heatmap horizontal scroll + compact cards on narrow viewports
- **Auth wall** — Supabase Auth or Edge Function with server-rendered token if dashboard goes semi-public
- **Comment collection** — extend scraper to fetch comment threads (t1 type) for richer signal
- **Sentiment modal** — click sentiment badge to open inline tooltip/modal with full supporting_quote context
- **utils.py** — extract shared logic: `mentions_competitor` regex (duplicated in scraper.py and analyzer.py), Supabase client init, env-var loading, and the COMPETITORS/FEATURES constants so all three files share a single source of truth
- **Unbounded DB queries** — `build_skip_set` and `fetch_unanalyzed` in analyzer.py fetch all rows with no server-side filter; push the skip-set join into Postgres (`.not_("id", "in", subquery)`) before data volume makes these slow
- **Anthropic error handling** — wrap `call_haiku` in `try/except anthropic.APIError` so rate-limit and network errors skip the batch gracefully instead of crashing the run

## Cost rules
1. String-match pre-filter before any API call
2. Skip already-analyzed mention_ids
3. Batch exactly 25 mentions per Haiku call
4. claude-haiku-4-5 only
5. Haiku extracts only — no sentiment scoring in prompt
6. Log token usage + estimated cost per batch
7. cardiffnlp pipeline loaded once per process

## GitHub Actions (.github/workflows/run.yml)
**v1: manual trigger only** (`workflow_dispatch`). No scheduled cron — scraping is initiated from the dashboard UI.
> TODO: Add a "Run Collection" button to index.html (Dashboard tab or Keyword Manager tab) that triggers
> `workflow_dispatch` via the GitHub Actions API. Button should show run status and disable while in progress.
> Add the cron schedule (`0 6 * * *`) only after validating manual run volume and cost.

Install torch CPU: `pip install torch --index-url https://download.pytorch.org/whl/cpu`
Cache ~/.cache/huggingface between runs.
Secrets: SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY, REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET
Runs: scraper.py → analyzer.py. No HTML commit step.

## Env vars
```
SUPABASE_URL          # Settings → API → Project URL
SUPABASE_KEY          # Settings → API → anon public  (safe in client JS with RLS)
SUPABASE_SERVICE_KEY  # Settings → API → service_role (Python only, never client-side)
ANTHROPIC_API_KEY     # console.anthropic.com → API Keys
REDDIT_CLIENT_ID      # reddit.com/prefs/apps → script app → client_id (under app name)
REDDIT_CLIENT_SECRET  # reddit.com/prefs/apps → script app → secret
```
Reddit token is ephemeral (1h TTL) — fetch at scraper startup, never store as a secret.

## Everything Claude Code conventions
For every component: /documentation-lookup → /prp-prd → /prp-plan → /prp-implement → /code-review
Use --parallel on /prp-plan for large features.