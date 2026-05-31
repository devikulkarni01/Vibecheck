# Plan: scraper.py — Reddit Mention Scraper

## Summary

A single Python script that fetches a Reddit OAuth token at startup, reads active search terms from the Supabase `search_terms` table, runs a (term × subreddit) search matrix with up to 3 pages of pagination per pair, applies popularity thresholds, and upserts qualifying rows to `raw_mentions`. Idempotent (upsert on `id`), rate-limited (1 req/s), resilient (exponential backoff on 429), and safe to test locally via `--dry-run`.

## User Story

As the GitHub Actions cron runner (or a developer running locally),
I want to collect fresh Reddit mentions for every active search term across all configured subreddits,
So that `raw_mentions` is populated daily and downstream analysis has data to process.

## Problem → Solution

No scraper exists; `raw_mentions` is empty → `scraper.py` runs daily, fetches up to N×M×3×100 candidate posts, filters and upserts qualifying ones, and logs a run summary.

## Metadata

- **Complexity**: Medium
- **Source PRD**: `.claude/PRPs/prds/scraper.prd.md`
- **PRD Phase**: All phases (1–5) collapsed into one implementation file
- **Estimated Files**: 1 (`scraper.py`) + 1 (`requirements.txt` update)

---

## UX Design

Internal change — no user-facing UX transformation. Developer UX:

### Before
```
$ python scraper.py
FileNotFoundError / ModuleNotFoundError  (file doesn't exist)
```

### After
```
$ python scraper.py --dry-run
[INFO] Reddit token acquired (expires in 3600s)
[INFO] Loaded 18 active search terms
[INFO] [Toggl × freelance] page 1: 100 candidates, 12 passed threshold → DRY RUN, not upserted
...
[SUMMARY] Terms: 18 | Subreddits: 15 | Requests: 162 | Rows upserted: 0 (dry-run) | 429s: 0

$ python scraper.py
[INFO] Reddit token acquired (expires in 3600s)
[INFO] Loaded 18 active search terms
[INFO] [Toggl × freelance] page 1: 100 candidates, 12 passed threshold → 12 upserted
...
[SUMMARY] Terms: 18 | Subreddits: 15 | Requests: 162 | Rows upserted: 847 | 429s: 0
```

### Interaction Changes

| Touchpoint | Before | After |
|---|---|---|
| GitHub Actions cron | scraper.py missing → pipeline fails | scraper.py runs, populates raw_mentions |
| Local dev | No way to test scrape | `--dry-run` prints rows without writing |
| Keyword manager | Terms added but never read | Terms read at runtime each run |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `CLAUDE.md` | 78–109 | Full Reddit OAuth spec, search endpoint, pagination, thresholds, upsert pattern |
| P0 | `schema.sql` | 14–23 | `raw_mentions` column names and types |
| P0 | `schema.sql` | 40–46 | `search_terms` column names — what `fetch_active_terms()` reads |
| P1 | `CLAUDE.md` | 46–59 | Supabase Python client pattern, upsert call signature |
| P1 | `CLAUDE.md` | 190–205 | Env var names, GH Actions secrets list |

## External Documentation

| Topic | Key Takeaway |
|---|---|
| Reddit OAuth (client_credentials) | POST to `www.reddit.com/api/v1/access_token` with Basic auth; response `{"access_token": "...", "expires_in": 3600}` |
| Reddit search endpoint | `GET oauth.reddit.com/r/{sub}/search?q=...&restrict_sr=true&limit=100&sort=relevance&t=all&after={cursor}` — must use `oauth.reddit.com`, not `www` |
| Reddit response shape | `response["data"]["children"]` → list of `{"kind": "t3", "data": {...}}`; `response["data"]["after"]` is next cursor or `null` |
| Reddit rate limit | 60 req/min with OAuth; `time.sleep(1)` between every page request is sufficient |
| Supabase upsert | `.upsert(row, on_conflict="id").execute()` — service role key bypasses RLS, no conflict error raised |

---

## Patterns to Mirror

### LOGGING_PATTERN
```python
# Prefix all log lines with level tag; flush stdout for GH Actions line buffering
print(f"[INFO] Reddit token acquired (expires in {expires_in}s)", flush=True)
print(f"[WARN] search_terms table is empty — nothing to scrape", flush=True)
print(f"[ERROR] 429 on attempt {attempt}/3 — backing off {backoff}s", flush=True)
```

### ENV_LOADING_PATTERN
```python
import os
from dotenv import load_dotenv  # optional for local dev; GH Actions sets env directly

load_dotenv()  # no-op if .env absent

SUPABASE_URL        = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
REDDIT_CLIENT_ID    = os.environ["REDDIT_CLIENT_ID"]
REDDIT_CLIENT_SECRET = os.environ["REDDIT_CLIENT_SECRET"]
```
Fail fast with `KeyError` if a required var is missing — do NOT use `.get()` with a default.

### SUPABASE_CLIENT_PATTERN
```python
# SOURCE: CLAUDE.md lines 48–55
from supabase import create_client
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Upsert by Reddit ID
supabase.table("raw_mentions").upsert(row, on_conflict="id").execute()

# Read active terms
resp = supabase.table("search_terms").select("term").eq("active", True).execute()
terms = [r["term"] for r in resp.data]
```

### REDDIT_AUTH_PATTERN
```python
import requests
from requests.auth import HTTPBasicAuth

def get_reddit_token() -> tuple[str, int]:
    resp = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        data={"grant_type": "client_credentials"},
        auth=HTTPBasicAuth(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    return body["access_token"], body["expires_in"]
```

### REDDIT_SEARCH_PATTERN
```python
def search_subreddit(token: str, subreddit: str, term: str, after: str | None) -> dict:
    params = {
        "q": term,
        "restrict_sr": "true",
        "limit": 100,
        "sort": "relevance",
        "t": "all",
    }
    if after:
        params["after"] = after
    resp = requests.get(
        f"https://oauth.reddit.com/r/{subreddit}/search",
        params=params,
        headers={"Authorization": f"bearer {token}", "User-Agent": USER_AGENT},
        timeout=10,
    )
    return resp  # caller checks status_code
```

### RETRY_BACKOFF_PATTERN
```python
import time

def request_with_retry(fn, *args, **kwargs):
    """Call fn(*args, **kwargs); retry on 429 with exponential backoff."""
    backoff = 5
    for attempt in range(1, 4):  # max 3 retries
        resp = fn(*args, **kwargs)
        if resp.status_code != 429:
            return resp
        print(f"[ERROR] 429 on attempt {attempt}/3 — backing off {backoff}s", flush=True)
        time.sleep(backoff)
        backoff *= 2
    return resp  # return last response; caller handles non-200
```

### ROW_BUILD_PATTERN
```python
# SOURCE: CLAUDE.md lines 115–126 + schema.sql lines 14–23
from datetime import datetime, timezone

def build_row(child: dict, subreddit: str) -> dict:
    d = child["data"]
    return {
        "id":                child["name"],   # Reddit fullname, e.g. "t3_abc123"
        "subreddit":         subreddit,
        "type":              "post" if child["kind"] == "t3" else "comment",
        "body":              d.get("selftext") or d.get("body", ""),
        "score":             d.get("score", 0),
        "url":               "https://reddit.com" + d.get("permalink", ""),
        "reddit_created_at": datetime.fromtimestamp(
                                 d["created_utc"], tz=timezone.utc
                             ).isoformat(),
    }
```

Note: `schema.sql` uses `type text` with values `'post'` or `'comment'`, NOT `'t3'`/`'t1'`.

### THRESHOLD_PATTERN
```python
POST_MIN_SCORE    = 5
POST_MIN_COMMENTS = 2
COMMENT_MIN_SCORE = 2

def passes_threshold(child: dict) -> bool:
    d = child["data"]
    kind = child["kind"]
    if kind == "t3":  # post
        return d.get("score", 0) >= POST_MIN_SCORE and d.get("num_comments", 0) >= POST_MIN_COMMENTS
    if kind == "t1":  # comment
        return d.get("score", 0) >= COMMENT_MIN_SCORE
    return False
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `scraper.py` | CREATE | New script — does not exist yet |
| `requirements.txt` | CREATE or UPDATE | Add `requests`, `supabase`, `python-dotenv` |

## NOT Building

- Pagination beyond 1 page per (term × subreddit) — MAX_PAGES = 1 in v1 caps at 270 req/run; bump later after validating volume
- Automatic scheduled cron — v1 is manual-trigger only; cron added after volume validated
- Async / parallel requests — rate limit makes concurrency irrelevant
- PRAW or any Reddit wrapper library — plain `requests` only
- Incremental cursor persistence — full re-search each run; upsert handles dedup
- Proxy rotation or CAPTCHA bypass

---

## Step-by-Step Tasks

### Task 1: Constants and env loading

- **ACTION**: Define module-level constants and load env vars with fail-fast validation.
- **IMPLEMENT**:
  ```python
  import os, argparse, time
  from datetime import datetime, timezone
  import requests
  from requests.auth import HTTPBasicAuth
  from supabase import create_client

  USER_AGENT = "script:CompetitiveIntelBot:v1.0 (by /u/Real_Experience_3832)"

  SUBREDDITS = [
      "freelance", "freelancedesign", "consulting", "smallbusiness", "webdev",
      "graphic_design", "productivityapps", "workforcemanagement", "Entrepreneur",
      "productivity", "timetracking", "timetrackingsoftware", "remotework",
      "dataisbeautiful", "askreddit",
  ]

  POST_MIN_SCORE    = 5
  POST_MIN_COMMENTS = 2
  COMMENT_MIN_SCORE = 2
  MAX_PAGES         = 1  # v1 cap: 18 terms × 15 subs × 1 page = 270 req/run (~4.5 min)

  SUPABASE_URL         = os.environ["SUPABASE_URL"]
  SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
  REDDIT_CLIENT_ID     = os.environ["REDDIT_CLIENT_ID"]
  REDDIT_CLIENT_SECRET = os.environ["REDDIT_CLIENT_SECRET"]
  ```
- **GOTCHA**: Use `os.environ["KEY"]` not `os.getenv("KEY")` — KeyError on missing var is the desired fail-fast behavior. Do NOT add defaults.
- **GOTCHA**: `USER_AGENT` uses `u/Real_Experience_3832` — do not change.
- **VALIDATE**: `python -c "import scraper"` with env vars set prints nothing and exits 0. With a var unset, raises `KeyError`.

---

### Task 2: Argument parsing

- **ACTION**: Add `--dry-run` flag via `argparse`.
- **IMPLEMENT**:
  ```python
  def parse_args():
      parser = argparse.ArgumentParser(description="Reddit mention scraper")
      parser.add_argument("--dry-run", action="store_true",
                          help="Print rows without writing to Supabase")
      return parser.parse_args()
  ```
- **VALIDATE**: `python scraper.py --dry-run` does not error; `args.dry_run` is `True`.

---

### Task 3: Reddit OAuth token

- **ACTION**: Implement `get_reddit_token()` — fetches bearer token at startup.
- **IMPLEMENT**:
  ```python
  def get_reddit_token() -> str:
      resp = requests.post(
          "https://www.reddit.com/api/v1/access_token",
          data={"grant_type": "client_credentials"},
          auth=HTTPBasicAuth(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
          headers={"User-Agent": USER_AGENT},
          timeout=10,
      )
      resp.raise_for_status()
      body = resp.json()
      expires_in = body.get("expires_in", 3600)
      print(f"[INFO] Reddit token acquired (expires in {expires_in}s)", flush=True)
      return body["access_token"]
  ```
- **GOTCHA**: Must call `www.reddit.com` for token, but `oauth.reddit.com` for search — different hosts.
- **VALIDATE**: With real credentials, prints `[INFO] Reddit token acquired...` and returns a non-empty string.

---

### Task 4: Fetch active search terms from Supabase

- **ACTION**: Implement `fetch_active_terms(supabase_client)` — reads `search_terms` where `active = true`.
- **IMPLEMENT**:
  ```python
  def fetch_active_terms(db) -> list[str]:
      resp = db.table("search_terms").select("term").eq("active", True).execute()
      terms = [r["term"] for r in resp.data]
      if not terms:
          print("[WARN] search_terms table is empty — nothing to scrape", flush=True)
      else:
          print(f"[INFO] Loaded {len(terms)} active search terms", flush=True)
      return terms
  ```
- **VALIDATE**: Against the seeded DB, returns a list of 18 competitor names.

---

### Task 5: Reddit search with retry

- **ACTION**: Implement `search_page(token, subreddit, term, after)` — single page fetch with 429 retry.
- **IMPLEMENT**:
  ```python
  def search_page(token: str, subreddit: str, term: str, after: str | None) -> requests.Response:
      params = {"q": term, "restrict_sr": "true", "limit": 100,
                "sort": "relevance", "t": "all"}
      if after:
          params["after"] = after
      headers = {"Authorization": f"bearer {token}", "User-Agent": USER_AGENT}
      url = f"https://oauth.reddit.com/r/{subreddit}/search"

      backoff = 5
      for attempt in range(1, 4):
          resp = requests.get(url, params=params, headers=headers, timeout=10)
          if resp.status_code != 429:
              return resp
          print(f"[ERROR] 429 on attempt {attempt}/3 for [{term} × {subreddit}] — backing off {backoff}s",
                flush=True)
          time.sleep(backoff)
          backoff *= 2
      return resp  # exhausted retries; caller handles non-200
  ```
- **GOTCHA**: Endpoint is `oauth.reddit.com`, not `www.reddit.com`.
- **GOTCHA**: After 3 failed retries, return the last response and let the caller log and continue — do NOT raise.
- **VALIDATE**: Mock a 200 response; verify params include `restrict_sr=true`.

---

### Task 6: Popularity threshold filter

- **ACTION**: Implement `passes_threshold(child)` — returns True for qualifying posts and comments.
- **IMPLEMENT**:
  ```python
  def passes_threshold(child: dict) -> bool:
      d = child["data"]
      kind = child["kind"]
      if kind == "t3":
          return (d.get("score", 0) >= POST_MIN_SCORE
                  and d.get("num_comments", 0) >= POST_MIN_COMMENTS)
      if kind == "t1":
          return d.get("score", 0) >= COMMENT_MIN_SCORE
      return False
  ```
- **VALIDATE**: `passes_threshold({"kind": "t3", "data": {"score": 3, "num_comments": 5}})` → `False` (score too low). `passes_threshold({"kind": "t3", "data": {"score": 10, "num_comments": 3}})` → `True`.

---

### Task 7: Row builder

- **ACTION**: Implement `build_row(child, subreddit)` — maps Reddit response to `raw_mentions` schema.
- **IMPLEMENT**:
  ```python
  def build_row(child: dict, subreddit: str) -> dict:
      d = child["data"]
      return {
          "id":                child["name"],
          "subreddit":         subreddit,
          "type":              "post" if child["kind"] == "t3" else "comment",
          "body":              d.get("selftext") or d.get("body") or "",
          "score":             d.get("score", 0),
          "url":               "https://reddit.com" + d.get("permalink", ""),
          "reddit_created_at": datetime.fromtimestamp(
                                   d["created_utc"], tz=timezone.utc
                               ).isoformat(),
      }
  ```
- **GOTCHA**: `schema.sql` column `type` expects `'post'` or `'comment'`, NOT `'t3'`/`'t1'`.
- **GOTCHA**: `selftext` is used for posts; `body` for comments. Chain them with `or` — empty string `""` is falsy in Python.
- **GOTCHA**: `scraped_at` is NOT included — Supabase `DEFAULT now()` sets it automatically.
- **VALIDATE**: Output dict has exactly these keys: `id`, `subreddit`, `type`, `body`, `score`, `url`, `reddit_created_at`.

---

### Task 8: Upsert helper

- **ACTION**: Implement `upsert_row(db, row, dry_run)` — writes or prints the row.
- **IMPLEMENT**:
  ```python
  def upsert_row(db, row: dict, dry_run: bool) -> bool:
      if dry_run:
          print(f"[DRY RUN] {row['id']} | {row['subreddit']} | score={row['score']}", flush=True)
          return True
      try:
          db.table("raw_mentions").upsert(row, on_conflict="id").execute()
          return True
      except Exception as e:
          print(f"[ERROR] upsert failed for {row['id']}: {e}", flush=True)
          return False
  ```
- **VALIDATE**: With `dry_run=True`, nothing is written to DB. With `dry_run=False`, row appears in `raw_mentions`.

---

### Task 9: Main scrape loop

- **ACTION**: Implement `main()` — orchestrates the full (term × subreddit) matrix with pagination.
- **IMPLEMENT**:
  ```python
  def main():
      args = parse_args()
      db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
      token = get_reddit_token()
      terms = fetch_active_terms(db)
      if not terms:
          return

      total_requests = 0
      total_upserted = 0
      total_429s = 0

      for term in terms:
          for subreddit in SUBREDDITS:
              after = None
              for page in range(1, MAX_PAGES + 1):  # v1: 1 page per pair
                  time.sleep(1)         # 1 req/s rate limit
                  resp = search_page(token, subreddit, term, after)
                  total_requests += 1

                  if resp.status_code == 429:
                      total_429s += 1
                      print(f"[WARN] Skipping [{term} × {subreddit}] page {page} after retry exhaustion",
                            flush=True)
                      break
                  if resp.status_code != 200:
                      print(f"[WARN] HTTP {resp.status_code} for [{term} × {subreddit}] — skipping",
                            flush=True)
                      break

                  data = resp.json().get("data", {})
                  children = data.get("children", [])
                  after = data.get("after")

                  passed = [c for c in children if passes_threshold(c)]
                  print(f"[INFO] [{term} × {subreddit}] page {page}: "
                        f"{len(children)} candidates, {len(passed)} passed threshold",
                        flush=True)

                  for child in passed:
                      try:
                          row = build_row(child, subreddit)
                      except (KeyError, TypeError) as e:
                          print(f"[WARN] Skipping malformed child {child.get('name','?')}: {e}",
                                flush=True)
                          continue
                      if upsert_row(db, row, args.dry_run):
                          total_upserted += 1

                  if not after:
                      break  # no more pages

      suffix = " (dry-run)" if args.dry_run else ""
      print(f"\n[SUMMARY] Terms: {len(terms)} | Subreddits: {len(SUBREDDITS)} | "
            f"Requests: {total_requests} | Rows upserted: {total_upserted}{suffix} | "
            f"429s: {total_429s}",
            flush=True)
  ```
- **GOTCHA**: `time.sleep(1)` is called BEFORE the request, so even page-1 of the first (term, sub) is rate-limited. This is intentional — the Reddit token fetch doesn't count toward the 60/min OAuth limit.
- **GOTCHA**: Early-break on `not after` is inside the page loop, after processing children. A page with 0 results still sets `after = None`.
- **GOTCHA**: Wrap `build_row` in try/except — a malformed child must not abort the entire run.
- **VALIDATE**: `--dry-run` prints `[SUMMARY]` with `Rows upserted: 0 (dry-run)`. Live run shows count > 0 for real terms.

---

### Task 10: Entry point and requirements.txt

- **ACTION**: Add `if __name__ == "__main__": main()` and create `requirements.txt`.
- **IMPLEMENT** (requirements.txt):
  ```
  requests>=2.31.0
  supabase>=2.0.0
  python-dotenv>=1.0.0
  ```
  Note: `anthropic`, `transformers`, `torch` belong to `analyzer.py` — do NOT add here unless requirements.txt is shared (in which case add all).
- **VALIDATE**: `pip install -r requirements.txt` succeeds in a clean venv.

---

## Testing Strategy

### Manual Validation (primary)

This is a single-file script with external I/O — unit tests require mocking both `requests` and `supabase`. Manual validation against real credentials is the primary gate.

### Unit Tests (if added)

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `passes_threshold` post passes | score=10, num_comments=5 | True | No |
| `passes_threshold` post score too low | score=3, num_comments=5 | False | Yes |
| `passes_threshold` post comments too low | score=10, num_comments=1 | False | Yes |
| `passes_threshold` comment passes | kind=t1, score=3 | True | No |
| `passes_threshold` comment score too low | kind=t1, score=1 | False | Yes |
| `passes_threshold` unknown kind | kind=t2 | False | Yes |
| `build_row` post | kind=t3 with selftext | type="post", body=selftext | No |
| `build_row` post empty selftext | selftext="" | body="" | Yes |
| `build_row` missing created_utc | no created_utc key | KeyError (caught by caller) | Yes |

### Edge Cases Checklist

- [ ] `search_terms` table is empty → prints `[WARN]` and exits cleanly
- [ ] All pages for a (term, sub) return 0 children → no upserts, moves on
- [ ] Reddit returns `after: null` on page 1 → stops after 1 page
- [ ] 429 after 3 retries → logs `[WARN]`, increments `total_429s`, breaks to next subreddit
- [ ] Non-200, non-429 response → logs `[WARN]`, breaks to next subreddit
- [ ] `build_row` receives malformed child (missing keys) → `KeyError` caught, logs `[WARN]`, continues
- [ ] `--dry-run` → `total_upserted` still increments (counts would-be upserts), but no DB writes
- [ ] Supabase upsert raises exception → logs `[ERROR]`, continues

---

## Validation Commands

### Static Analysis
```bash
python -m py_compile scraper.py
```
EXPECT: No output (no syntax errors)

### Import check
```bash
python -c "import scraper" 2>&1 || true
```
EXPECT: `KeyError: 'SUPABASE_URL'` (expected — env not set) OR no error if env is set

### Dry-run smoke test
```bash
SUPABASE_URL=... SUPABASE_SERVICE_KEY=... REDDIT_CLIENT_ID=... REDDIT_CLIENT_SECRET=... \
  python scraper.py --dry-run
```
EXPECT: `[INFO] Reddit token acquired`, `[INFO] Loaded N active search terms`, `[DRY RUN]` lines, `[SUMMARY] ... Rows upserted: 0 (dry-run)`

### Live smoke test
```bash
SUPABASE_URL=... SUPABASE_SERVICE_KEY=... REDDIT_CLIENT_ID=... REDDIT_CLIENT_SECRET=... \
  python scraper.py
```
EXPECT: `[SUMMARY] ... Rows upserted: N` where N > 0

### DB verification after live run
```sql
SELECT COUNT(*) FROM raw_mentions WHERE scraped_at >= now() - interval '5 minutes';
SELECT type, COUNT(*) FROM raw_mentions GROUP BY type;
```
EXPECT: Count > 0; type values are `'post'` not `'t3'`

### Manual Validation Checklist

- [ ] `python scraper.py --dry-run` completes without error and prints `[SUMMARY]`
- [ ] `[SUMMARY]` shows `Rows upserted: 0 (dry-run)`
- [ ] `python scraper.py` (live) shows `Rows upserted: N` where N > 0
- [ ] DB query confirms `type` column contains `'post'` not `'t3'`
- [ ] DB query confirms `scraped_at` is auto-set by Supabase (not in row dict)
- [ ] Re-running live scraper does not raise errors (upsert ON CONFLICT)

---

## Acceptance Criteria

- [ ] All 9 functions implemented (`parse_args`, `get_reddit_token`, `fetch_active_terms`, `search_page`, `passes_threshold`, `build_row`, `upsert_row`, `main`)
- [ ] `--dry-run` writes nothing to DB
- [ ] Rate limit: `time.sleep(1)` before every search request
- [ ] 429 retry: exponential backoff 5→10→20s, max 3 retries, then skip + log
- [ ] Pagination: up to 3 pages per (term, subreddit) pair, stops early if `after` is null
- [ ] Threshold filter applied before upsert (POST_MIN_SCORE=5, POST_MIN_COMMENTS=2)
- [ ] Row `type` field uses `'post'`/`'comment'`, not `'t3'`/`'t1'`
- [ ] `scraped_at` NOT in row dict (DB default handles it)
- [ ] `[SUMMARY]` line printed at end of every run
- [ ] Malformed children logged and skipped, run does not abort

## Completion Checklist

- [ ] Single file, no classes, functional style
- [ ] `flush=True` on all `print()` calls (required for GH Actions log streaming)
- [ ] `os.environ["KEY"]` not `os.getenv("KEY")` for required vars
- [ ] Token fetched from `www.reddit.com`, search from `oauth.reddit.com`
- [ ] `requirements.txt` updated with `requests`, `supabase`, `python-dotenv`
- [ ] No hardcoded competitor names or search terms (always read from DB)
- [ ] USER_AGENT has a TODO comment for the actual Reddit username

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Reddit blocks User-Agent without real username | M | H | TODO comment in file; document in README |
| Token TTL (1h) exceeded on very large term set | L | M | At 1 req/s, 18 terms × 15 subs × 3 pages = 810 requests = 13.5 min — well within 1h |
| `search_terms` seeded but DB not applied | L | H | `schema.sql` must be applied first; scraper logs `[WARN]` and exits if empty |
| Supabase free tier row limit | L | L | Daily upsert volume is bounded; existing rows are overwritten by ON CONFLICT |

## Notes

- `USER_AGENT` uses `u/Real_Experience_3832` (confirmed username).
- `python-dotenv` is included for local dev convenience (loads `.env` file); in GH Actions, env vars are set directly from secrets.
- v1 is manual-trigger only. The dashboard needs a "Run Collection" button that calls `workflow_dispatch` — tracked as a TODO in CLAUDE.md.
- MAX_PAGES = 1 gives 270 req/run (~4.5 min at 1 req/s). Increase to 2 or 3 after validating volume.
- `analyzer.py` is NOT in scope — this plan covers `scraper.py` only.
