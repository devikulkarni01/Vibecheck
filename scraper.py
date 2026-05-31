import os
import re
import argparse
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

USER_AGENT = "script:CompetitiveIntelBot:v1.0 (by /u/Real_Experience_3832)"

SUBREDDITS = [
    "freelance",
    "freelancedesign",
    "consulting",
    "smallbusiness",
    "webdev",
    "graphic_design",
    "productivityapps",
    "workforcemanagement",
    "Entrepreneur",
    "productivity",
    "timetracking",
    "timetrackingsoftware",
    "remotework",
    "dataisbeautiful",
    "askreddit",
]

POST_MIN_SCORE = 5
POST_MIN_COMMENTS = 2
COMMENT_MIN_SCORE = 2
MAX_PAGES = 1  # v1 cap: ~18 terms × 15 subs × 1 page = 270 req/run (~4.5 min)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
REDDIT_CLIENT_ID = os.environ["REDDIT_CLIENT_ID"]
REDDIT_CLIENT_SECRET = os.environ["REDDIT_CLIENT_SECRET"]


def parse_args():
    parser = argparse.ArgumentParser(description="Reddit mention scraper")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print rows that would be upserted without writing to Supabase",
    )
    return parser.parse_args()


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
    if "access_token" not in body:
        raise RuntimeError(f"Reddit auth failed: {body.get('error', body)}")
    print(f"[INFO] Reddit token acquired (expires in {expires_in}s)", flush=True)
    return body["access_token"]


def fetch_active_terms(db) -> list[str]:
    resp = db.table("search_terms").select("term").eq("active", True).execute()
    terms = [r["term"] for r in resp.data]
    if not terms:
        print("[WARN] search_terms table is empty — nothing to scrape", flush=True)
    else:
        print(f"[INFO] Loaded {len(terms)} active search terms", flush=True)
    return terms


def search_page(
    token: str, subreddit: str, term: str, after: Optional[str]
) -> requests.Response:
    params = {
        "q": term,
        "restrict_sr": "true",
        "limit": 100,
        "sort": "relevance",
        "t": "all",
    }
    if after:
        params["after"] = after

    headers = {"Authorization": f"bearer {token}", "User-Agent": USER_AGENT}
    url = f"https://oauth.reddit.com/r/{subreddit}/search"

    backoff = 5
    for attempt in range(1, 4):
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code != 429:
            return resp
        print(
            f"[ERROR] 429 on attempt {attempt}/3 for [{term} × {subreddit}]"
            f" — backing off {backoff}s",
            flush=True,
        )
        time.sleep(backoff)
        backoff *= 2

    return resp  # exhausted retries; caller handles non-200


def mentions_competitor(body: str, terms: list) -> bool:
    return any(
        re.search(r"\b" + re.escape(term) + r"\b", body, re.IGNORECASE)
        for term in terms
    )


def passes_threshold(child: dict) -> bool:
    d = child["data"]
    kind = child["kind"]
    if kind == "t3":  # post
        return (
            d.get("score", 0) >= POST_MIN_SCORE
            and d.get("num_comments", 0) >= POST_MIN_COMMENTS
        )
    if kind == "t1":  # comment
        return d.get("score", 0) >= COMMENT_MIN_SCORE
    return False


def build_row(child: dict, subreddit: str) -> dict:
    d = child["data"]
    return {
        "id": d["name"],  # Reddit fullname, e.g. "t3_abc123"
        "subreddit": subreddit,
        "type": "post" if child["kind"] == "t3" else "comment",
        "body": "\n\n".join(filter(None, [
            d.get("title", ""),
            "" if d.get("selftext") in ("[deleted]", "[removed]") else (d.get("selftext") or d.get("body") or ""),
        ])),
        "score": d.get("score", 0),
        "url": "https://reddit.com" + d.get("permalink", ""),
        "reddit_created_at": datetime.fromtimestamp(
            d["created_utc"], tz=timezone.utc
        ).isoformat(),
    }


def upsert_row(db, row: dict, dry_run: bool) -> bool:
    if dry_run:
        print(
            f"[DRY RUN] {row['id']} | {row['subreddit']} | "
            f"type={row['type']} | score={row['score']}",
            flush=True,
        )
        return True
    try:
        db.table("raw_mentions").upsert(row, on_conflict="id").execute()
        return True
    except Exception as e:
        print(f"[ERROR] upsert failed for {row['id']}: {e}", flush=True)
        return False


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
            for page in range(1, MAX_PAGES + 1):
                time.sleep(1)  # 1 req/s — well within 60/min OAuth limit
                resp = search_page(token, subreddit, term, after)
                total_requests += 1

                if resp.status_code == 429:
                    total_429s += 1
                    print(
                        f"[WARN] Skipping [{term} × {subreddit}] page {page}"
                        f" after retry exhaustion",
                        flush=True,
                    )
                    break
                if resp.status_code != 200:
                    print(
                        f"[WARN] HTTP {resp.status_code} for"
                        f" [{term} × {subreddit}] — skipping",
                        flush=True,
                    )
                    break

                data = resp.json().get("data", {})
                children = data.get("children", [])
                after = data.get("after")

                passed = [c for c in children if passes_threshold(c)]
                print(
                    f"[INFO] [{term} × {subreddit}] page {page}:"
                    f" {len(children)} candidates, {len(passed)} passed threshold",
                    flush=True,
                )

                for child in passed:
                    try:
                        row = build_row(child, subreddit)
                    except (KeyError, TypeError) as e:
                        print(
                            f"[WARN] Skipping malformed child"
                            f" {child.get('data', {}).get('name', '?')}: {e}",
                            flush=True,
                        )
                        continue
                    if not mentions_competitor(row["body"], terms):
                        continue
                    if upsert_row(db, row, args.dry_run):
                        total_upserted += 1

                if not after:
                    break  # no more pages for this (term, subreddit) pair

    suffix = " (dry-run)" if args.dry_run else ""
    print(
        f"\n[SUMMARY] Terms: {len(terms)} | Subreddits: {len(SUBREDDITS)} |"
        f" Requests: {total_requests} | Rows upserted: {total_upserted}{suffix}"
        f" | 429s: {total_429s}",
        flush=True,
    )


if __name__ == "__main__":
    main()
