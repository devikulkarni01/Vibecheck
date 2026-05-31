# Reddit JSON API — Reference

Sources:
- https://github.com/reddit-archive/reddit/wiki/JSON
- https://github.com/reddit-archive/reddit/wiki/API
- https://github.com/reddit-archive/reddit/wiki/OAuth2
- https://praw.readthedocs.io/en/stable/getting_started/authentication.html
- Live API testing: 2026-05-31

---

## Critical: Unauthenticated Access is Blocked (as of 2023)

Reddit's June 2023 API policy update effectively ended reliable unauthenticated access to
`reddit.com/*.json` endpoints from server-side scripts. Requests without a valid OAuth
bearer token now return **HTTP 403** regardless of User-Agent.

**What this means for `scraper.py`:** The CLAUDE.md spec lists the unauthenticated
JSON endpoint, but it will not work reliably in production or GitHub Actions.
**OAuth application-only auth is required.**

---

## Response Shape

All Reddit API responses are "Things" — typed JSON objects:

```json
{
  "kind": "Listing",
  "data": {
    "after":    "t3_abc123",   // cursor for next page (null if last page)
    "before":   null,           // cursor for previous page
    "dist":     25,             // number of items in this response
    "modhash":  "",
    "geo_filter": null,
    "children": [
      {
        "kind": "t3",           // t1=comment, t2=account, t3=post, t4=message, t5=subreddit
        "data": {
          "id":          "abc123",
          "name":        "t3_abc123",     // fullname = kind_prefix + id
          "title":       "Post title",
          "selftext":    "Post body text",
          "score":       42,
          "num_comments": 7,
          "subreddit":   "timetracking",
          "permalink":   "/r/timetracking/comments/abc123/post_title/",
          "url":         "https://www.reddit.com/r/...",
          "created_utc": 1700000000.0,
          "author":      "username",
          "is_self":     true
        }
      }
    ]
  }
}
```

**Key `kind` prefixes:**
| Prefix | Type |
|---|---|
| `t1` | Comment |
| `t2` | Account |
| `t3` | Link / Post |
| `t4` | Message |
| `t5` | Subreddit |
| `t6` | Award |

For search results, children are `t3` (posts). To get comments for a post, fetch
`/r/{sub}/comments/{post_id}.json` — the second element of the array response is
the comment listing.

---

## Search Endpoint

```
GET https://oauth.reddit.com/r/{subreddit}/search
  ?q={term}
  &restrict_sr=true
  &limit=100
  &sort=relevance
  &t=all
  &after={cursor}      # for pagination
```

**Use `oauth.reddit.com`** (not `www.reddit.com`) when sending an OAuth bearer token.

### Parameters

| Param | Values | Notes |
|---|---|---|
| `q` | search string | Supports `title:`, `selftext:`, `author:` prefixes |
| `restrict_sr` | `true` / `false` | `true` = search only this subreddit |
| `limit` | 1–100 | Max 100 per request |
| `sort` | `relevance`, `hot`, `top`, `new`, `comments` | `relevance` best for competitive intel |
| `t` | `hour`, `day`, `week`, `month`, `year`, `all` | Time range |
| `after` | fullname e.g. `t3_abc123` | Pagination cursor from previous response |
| `before` | fullname | Reverse pagination cursor |
| `count` | int | Total items seen so far (used with after/before) |

---

## Pagination

Reddit uses **cursor-based pagination** via fullnames (not page numbers or offsets).

```
Page 1: GET /search?q=toggl&limit=100
  → response.data.after = "t3_xyz999"  (or null if < 100 results)

Page 2: GET /search?q=toggl&limit=100&after=t3_xyz999
  → response.data.after = "t3_abc111"  (or null = last page)

Page 3: GET /search?q=toggl&limit=100&after=t3_abc111
  → response.data.after = null  ← stop here
```

**Implementation pattern for `scraper.py`:**

```python
after = None
for page in range(3):          # max 3 pages per CLAUDE.md spec
    params = {
        "q": term,
        "restrict_sr": "true",
        "limit": 100,
        "sort": "relevance",
        "t": "all",
    }
    if after:
        params["after"] = after

    response = requests.get(
        f"https://oauth.reddit.com/r/{subreddit}/search",
        headers=headers,
        params=params,
    )
    data = response.json()["data"]
    posts = [child["data"] for child in data["children"]]

    # process posts...

    after = data.get("after")
    if not after:
        break                  # no more pages
    time.sleep(1)              # rate limit courtesy delay
```

---

## Authentication (Required)

### Register a script app

1. Go to https://www.reddit.com/prefs/apps
2. Create a new app → type: **script**
3. Note the `client_id` (under the app name) and `client_secret`

### Get an application-only bearer token

No user login needed — use the **client_credentials** grant:

```python
import requests
import base64

def get_reddit_token(client_id: str, client_secret: str) -> str:
    auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
    data = {"grant_type": "client_credentials"}
    headers = {"User-Agent": "script:CompetitiveIntelBot:v1.0 (by /u/your_username)"}

    resp = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=auth,
        data=data,
        headers=headers,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]   # valid for 1 hour (3600s)
```

Token response shape:
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600,
  "scope": "*"
}
```

App-only tokens **never receive a refresh token** — re-request when expired.

### Use the token in scraping requests

```python
headers = {
    "Authorization": f"bearer {token}",
    "User-Agent": "script:CompetitiveIntelBot:v1.0 (by /u/your_username)",
}

resp = requests.get(
    f"https://oauth.reddit.com/r/{subreddit}/search",
    headers=headers,
    params=params,
)
```

---

## Rate Limits

| Auth level | Limit | Headers |
|---|---|---|
| **OAuth (authenticated)** | 60 requests / minute | `X-Ratelimit-Used`, `X-Ratelimit-Remaining`, `X-Ratelimit-Reset` |
| **Unauthenticated** | Effectively blocked (403) | — |

**Rate limit headers on each response:**
- `X-Ratelimit-Used` — requests used in current window
- `X-Ratelimit-Remaining` — requests remaining
- `X-Ratelimit-Reset` — seconds until window resets

**Retry on 429:**

```python
import time

def get_with_retry(url, headers, params, max_retries=3):
    delay = 5
    for attempt in range(max_retries):
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", delay))
            time.sleep(retry_after)
            delay *= 2
            continue
        resp.raise_for_status()
        return resp
    raise Exception(f"Failed after {max_retries} retries")
```

`time.sleep(1)` between pages keeps usage well within the 60 req/min limit.

---

## User-Agent Requirements

Reddit mandates this format for registered apps:

```
<platform>:<app_id>:<version> (by /u/<reddit_username>)
```

Example:
```
script:CompetitiveIntelBot:v1.0 (by /u/vibecheck_dev)
```

Rules:
- Must be **unique and descriptive** — generic UAs (`python-requests/2.x`) receive reduced limits or blocks
- **Never spoof** a browser User-Agent — bots caught doing this are banned
- Update the version string when the app changes significantly

---

## Comment scraping

To get comments on a post (for `type=comment` rows in `raw_mentions`):

```
GET https://oauth.reddit.com/r/{subreddit}/comments/{post_id}
```

Returns a two-element array:
- `[0]` — the post (Listing of t3)
- `[1]` — top-level comments (Listing of t1)

Comments have `body` field (not `selftext`). Nested replies are in `replies` → same
Listing structure recursively.

---

## Env vars to add

```
REDDIT_CLIENT_ID      # app client_id from reddit.com/prefs/apps
REDDIT_CLIENT_SECRET  # app client_secret
```

The token itself is ephemeral (1h TTL) and should be fetched at scraper startup,
not stored as a secret.
