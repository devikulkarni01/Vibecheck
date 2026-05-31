import os
import re
import json
import argparse
import time
from typing import Optional

import anthropic
from dotenv import load_dotenv
from supabase import create_client
from transformers import pipeline as hf_pipeline

load_dotenv()

# ─── Constants ────────────────────────────────────────────────────────────────

BATCH_SIZE = 25
MAX_QUOTE_WORDS = 50
MAX_WINDOW_CHARS = 800
SENTENCE_PRE_WINDOW = 1   # sentences before hit
SENTENCE_POST_WINDOW = 2  # sentences after hit
MAX_ROBERTA_TOKENS = 128
MODEL = "claude-haiku-4-5"
ROBERTA_MODEL = "cardiffnlp/twitter-roberta-base-topic-sentiment-latest"
HAIKU_INPUT_COST_PER_M = 0.80    # USD per million tokens
HAIKU_OUTPUT_COST_PER_M = 4.00

COMPETITORS = [
    "Rize", "Timely", "Reclaim.ai", "TimeCamp", "Memtime", "Timeular",
    "Clockk", "Hubstaff", "Toggl Track", "Toggl", "Clockify", "Harvest",
    "RescueTime", "Carly AI", "Replicon", "ZeroTime", "Kickidler", "Flowace",
]

FEATURES = [
    "automatic time tracking", "manual time tracking", "voice time tracking",
    "voice capture", "speech to text", "idle detection", "offline tracking",
    "timer accuracy", "background tracking", "browser extension", "desktop app",
    "mobile app", "GPS tracking", "invoicing", "billing", "payment processing",
    "expense tracking", "budget tracking", "AI features", "smart scheduling",
    "AI insights", "AI reports", "meeting detection", "calendar AI",
    "employee monitoring", "screenshots", "team analytics", "team dashboard",
    "project budgeting", "payroll integration", "geofencing",
    "calendar sync", "Jira integration", "Asana integration", "Slack integration",
    "QuickBooks integration", "Xero integration", "API access", "data export",
    "excel export", "Zapier integration", "project tool integrations",
    "accounting integrations", "ease of use", "onboarding", "customer support",
    "UI design", "pricing", "free tier", "privacy concerns",
    "surveillance concerns", "accuracy", "reliability",
]

# ─── Env vars — KeyError on missing = intentional fail-fast ──────────────────

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Populated in main() after arg parsing — None in dry-run mode
pipe = None

# ─── Haiku system prompt — built once at module level ────────────────────────

SYSTEM_PROMPT = (
    f"You extract structured data from Reddit text about time-tracking tools.\n"
    f"Competitors: {', '.join(COMPETITORS)}\n"
    f"Features: {', '.join(FEATURES)}\n"
    f"Return ONLY a valid JSON array, no preamble, no markdown fences, no explanation.\n"
    f'Each element: {{"mention_id": str, "competitor": str, "feature": str, "supporting_quote": str}}\n'
    f"One element per (competitor, feature) pair. If a mention signals multiple features for the same competitor, "
    f"create one separate element for each feature — do not combine features in a single element.\n"
    f"supporting_quote: ≤{MAX_QUOTE_WORDS} words, direct excerpt or close paraphrase from the text.\n"
    f"Only extract if the competitor name clearly refers to the time-tracking software product.\n"
    f"If the name appears in an unrelated context (e.g. 'harvest' as a verb, unrelated brand), "
    f"return [] for that mention.\n"
    f"Return [] if no signal found."
)


# ─── Argument parsing ─────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Mention analyzer: Haiku extraction + RoBERTa scoring"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run Haiku extraction only; print results without scoring or upserting",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of mentions processed in this run",
    )
    return parser.parse_args()


# ─── Data helpers ─────────────────────────────────────────────────────────────

def build_skip_set(db) -> set[str]:
    resp = db.table("mention_analyses").select("mention_id").execute()
    ids = {r["mention_id"] for r in resp.data}
    print(f"[INFO] Skip set built: {len(ids)} already-analyzed mention_ids", flush=True)
    return ids


def fetch_unanalyzed(db, skip_set: set[str], limit: Optional[int]) -> list[dict]:
    resp = db.table("raw_mentions").select("id, body, subreddit, url").execute()
    rows = [r for r in resp.data if r["id"] not in skip_set]
    if limit:
        rows = rows[:limit]
    print(f"[INFO] {len(rows)} unanalyzed mentions after skip-set filter", flush=True)
    return rows


def _mentions_any_competitor(text: str) -> bool:
    return any(
        re.search(r"\b" + re.escape(c) + r"\b", text, re.IGNORECASE)
        for c in COMPETITORS
    )


def filter_by_competitor(rows: list[dict]) -> list[dict]:
    matched = [r for r in rows if _mentions_any_competitor(r["body"])]
    print(
        f"[INFO] {len(matched)}/{len(rows)} mentions pass competitor string-match",
        flush=True,
    )
    return matched


# ─── Sentence windowing ───────────────────────────────────────────────────────

def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    sorted_ranges = sorted(ranges)
    merged = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def extract_window(body: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", body.strip())
    hit_indices = [
        i for i, s in enumerate(sentences)
        if _mentions_any_competitor(s)
    ]
    if not hit_indices:
        print("[WARN] No competitor-matching sentences found; using full body", flush=True)
        return body[:MAX_WINDOW_CHARS]

    ranges = [
        (
            max(0, i - SENTENCE_PRE_WINDOW),
            min(len(sentences) - 1, i + SENTENCE_POST_WINDOW),
        )
        for i in hit_indices
    ]
    merged = _merge_ranges(ranges)

    window_sentences = []
    for start, end in merged:
        window_sentences.extend(sentences[start:end + 1])

    window = " ".join(window_sentences)
    return window[:MAX_WINDOW_CHARS]


# ─── Haiku Layer 1 ───────────────────────────────────────────────────────────

def build_user_message(batch: list[dict]) -> str:
    lines = [f'[{item["mention_id"]}] {item["window"]}' for item in batch]
    return "\n\n".join(lines)


def call_haiku(client: anthropic.Anthropic, batch: list[dict]) -> list[dict]:
    user_msg = build_user_message(batch)

    for attempt in range(1, 3):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()

        # Defensive: strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        try:
            rows = json.loads(raw)
            in_t = response.usage.input_tokens
            out_t = response.usage.output_tokens
            cost = (in_t * HAIKU_INPUT_COST_PER_M + out_t * HAIKU_OUTPUT_COST_PER_M) / 1_000_000
            print(
                f"[INFO] Haiku tokens in={in_t} out={out_t} | est ${cost:.4f}",
                flush=True,
            )
            if not isinstance(rows, list):
                print(
                    f"[WARN] Haiku returned non-list JSON (type={type(rows).__name__}) — skipping batch",
                    flush=True,
                )
                return []
            return rows
        except json.JSONDecodeError:
            print(
                f"[WARN] Malformed JSON from Haiku (attempt {attempt}/2) — "
                + ("retrying" if attempt == 1 else "skipping batch"),
                flush=True,
            )
            if attempt == 1:
                time.sleep(1)

    return []


# ─── RoBERTa Layer 2 ─────────────────────────────────────────────────────────

def score_quote(quote: str) -> tuple[str, float]:
    result = pipe(quote, truncation=True, max_length=MAX_ROBERTA_TOKENS)[0]
    return result["label"], round(result["score"], 2)


# ─── Persistence ─────────────────────────────────────────────────────────────

def insert_batch(db, rows: list[dict], dry_run: bool) -> int:
    if not rows:
        return 0
    if dry_run:
        for r in rows:
            print(
                f"[DRY RUN] mention_id={r['mention_id']} competitor={r['competitor']}"
                f" feature={r['feature']} quote=\"{r['supporting_quote'][:60]}\"",
                flush=True,
            )
        return len(rows)
    try:
        db.table("mention_analyses").insert(rows).execute()
        return len(rows)
    except Exception as e:
        print(f"[ERROR] insert_batch failed: {e}", flush=True)
        return 0


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    global pipe
    args = parse_args()

    if not args.dry_run:
        print(f"[INFO] Loading model: {ROBERTA_MODEL}", flush=True)
        pipe = hf_pipeline("text-classification", model=ROBERTA_MODEL)
        print(f"[INFO] Model loaded", flush=True)

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    skip_set = build_skip_set(db)
    mentions = fetch_unanalyzed(db, skip_set, args.limit)
    mentions = filter_by_competitor(mentions)

    if not mentions:
        print("[INFO] No mentions to analyze — exiting", flush=True)
        return

    for m in mentions:
        m["mention_id"] = m["id"]
        m["window"] = extract_window(m["body"])

    batches = [mentions[i:i + BATCH_SIZE] for i in range(0, len(mentions), BATCH_SIZE)]
    total_batches = len(batches)
    total_rows_written = 0
    total_batches_skipped = 0

    for batch_num, batch in enumerate(batches, start=1):
        print(
            f"[INFO] Batch {batch_num}/{total_batches} — sending {len(batch)} mentions to Haiku",
            flush=True,
        )

        extracted = call_haiku(client, batch)

        if not extracted:
            print(
                f"[WARN] Batch {batch_num} produced no rows (malformed JSON or all [])",
                flush=True,
            )
            total_batches_skipped += 1

        if args.dry_run:
            for r in extracted:
                print(
                    f"[DRY RUN] mention_id={r.get('mention_id')}"
                    f" competitor={r.get('competitor')}"
                    f" feature={r.get('feature')}"
                    f"\n         quote: {r.get('supporting_quote', '')}",
                    flush=True,
                )
            continue

        output_rows = []
        for r in extracted:
            quote = r.get("supporting_quote", "").strip()
            if not quote:
                continue
            label, score = score_quote(quote)
            output_rows.append({
                "mention_id": r["mention_id"],
                "competitor": r["competitor"],
                "feature": r["feature"],
                "supporting_quote": quote,
                "sentiment_label": label,
                "sentiment_score": score,
            })

        written = insert_batch(db, output_rows, dry_run=False)
        total_rows_written += written
        print(
            f"[INFO] Batch {batch_num}/{total_batches} — {written} rows written",
            flush=True,
        )

    suffix = " (dry-run: Layer 1 only)" if args.dry_run else ""
    print(
        f"\n[SUMMARY] Batches: {total_batches} | Rows written: {total_rows_written}"
        f" | Batches skipped: {total_batches_skipped}{suffix}",
        flush=True,
    )


if __name__ == "__main__":
    main()
