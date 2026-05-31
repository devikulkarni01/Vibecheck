# Plan: analyzer.py — Two-Layer Mention Analysis Pipeline

## Summary

`analyzer.py` is a CLI pipeline that reads unanalyzed rows from `raw_mentions`, extracts competitor/feature signals via Haiku (Layer 1), scores each extracted quote with a local cardiffnlp RoBERTa model (Layer 2), and writes results to `mention_analyses`. `--dry-run` exits after Layer 1 so the operator can review Haiku extractions before committing to scoring and upsert.

## User Story

As an analyst running the competitive intelligence tool,
I want to run `python analyzer.py` after a scrape to automatically extract and score competitor mentions,
So that the dashboard Heatmap, Pain Points, and Opportunity Matrix tabs have data to display.

## Problem → Solution

`raw_mentions` is populated by `scraper.py` but `mention_analyses` stays empty → `analyzer.py` bridges raw Reddit text to structured `(competitor, feature, sentiment)` rows.

## Metadata

- **Complexity**: Medium
- **Source PRD**: `.claude/PRPs/prds/analyzer.prd.md`
- **PRD Phase**: All 5 phases (single file implementation)
- **Estimated Files**: 1 new file (`analyzer.py`), 1 updated (`requirements.txt`)

---

## UX Design

Internal change — no user-facing UX transformation. CLI operator experience:

### Before
```
$ python scraper.py
[SUMMARY] Rows upserted: 350
$ # nothing to analyze; dashboard is blank
```

### After
```
$ python analyzer.py --dry-run        # review Haiku extractions first
[INFO] Model loaded: cardiffnlp/twitter-roberta-base-topic-sentiment-latest
[INFO] 350 unanalyzed mentions after skip-set filter
[INFO] 312 mentions pass competitor string-match
[INFO] Batch 1/13 — Haiku extracted 18 rows | tokens in=4210 out=820 | est $0.0037
[DRY RUN] mention_id=t3_abc123 competitor=Toggl feature=calendar sync quote="Clean UI..."
...
[SUMMARY] Batches: 13 | Extracted rows: 187 | Batches skipped: 0

$ python analyzer.py                  # full run
[INFO] Model loaded ...
...
[SUMMARY] Batches: 13 | Rows written: 187 | Batches skipped: 0
```

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `scraper.py` | 1–13 | Module-level env loading + import pattern to mirror exactly |
| P0 | `scraper.py` | 40–43 | `os.environ["KEY"]` fail-fast pattern (no `.get()`) |
| P0 | `scraper.py` | 46–53 | `parse_args()` structure and `--dry-run` flag |
| P0 | `scraper.py` | 153–166 | `upsert_row()` — dry-run branch + try/except + `[ERROR]` log |
| P0 | `scraper.py` | 169–244 | `main()` entry point structure + `[SUMMARY]` footer |
| P0 | `scraper.py` | 69,77,104,165 | `print(f"[INFO/WARN/ERROR] ...", flush=True)` logging convention |
| P1 | `scraper.py` | 115–119 | `mentions_competitor()` — `re.search(r"\b" + re.escape(term) + r"\b", body, re.IGNORECASE)` |
| P1 | `scraper.py` | 99–112 | Retry loop with exponential backoff pattern |
| P1 | `schema.sql` | 17–30 | `mention_analyses` column names and types |
| P2 | `CLAUDE.md` | Analysis pipeline section | System prompt spec, batch size, cost formula, five RoBERTa labels |
| P2 | `CLAUDE.md` | Competitors + Features lists | Source of truth for COMPETITORS and FEATURES constants |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Anthropic SDK messages.create | CLAUDE.md + anthropic>=0.25.0 | Use `client.messages.create(model=, max_tokens=, system=, messages=[])` |
| cardiffnlp pipeline | CLAUDE.md + Documentation-Roberta.md | `pipeline("text-classification", model=ROBERTA_MODEL)` — load once; call as `pipe(text, truncation=True, max_length=128)` → `[{'label': str, 'score': float}]` |
| Supabase insert | CLAUDE.md + Documentation-Supabase.md | `db.table("mention_analyses").insert(rows).execute()` — no `on_conflict` needed (UUID PK) |
| Haiku pricing | CLAUDE.md cost formula | input: $0.80/1M tokens, output: $4.00/1M tokens |

---

## Patterns to Mirror

### NAMING_CONVENTION
```python
# SOURCE: scraper.py:15-43
# Module-level UPPER_SNAKE_CASE constants; no class wrapping; plain functions
USER_AGENT = "script:CompetitiveIntelBot:v1.0 (by /u/Real_Experience_3832)"
POST_MIN_SCORE = 5
SUPABASE_URL = os.environ["SUPABASE_URL"]

def fetch_active_terms(db) -> list[str]:   # snake_case functions, type hints
    ...
```

### ENV_LOADING
```python
# SOURCE: scraper.py:1-13, 40-43
load_dotenv()                              # at module top, before constants
SUPABASE_URL = os.environ["SUPABASE_URL"] # KeyError on missing = intentional fail-fast
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
# Never use os.environ.get() for required vars
```

### LOGGING_PATTERN
```python
# SOURCE: scraper.py:69,77,104,165,210,234
print(f"[INFO] Reddit token acquired (expires in {expires_in}s)", flush=True)
print(f"[WARN] search_terms table is empty — nothing to scrape", flush=True)
print(f"[ERROR] 429 on attempt {attempt}/3 for [{term} × {subreddit}]", flush=True)
# Always flush=True; always [LEVEL] prefix; f-string with context
```

### DRY_RUN_PATTERN
```python
# SOURCE: scraper.py:153-166
def upsert_row(db, row: dict, dry_run: bool) -> bool:
    if dry_run:
        print(f"[DRY RUN] {row['id']} | ...", flush=True)
        return True
    try:
        db.table("raw_mentions").upsert(row, on_conflict="id").execute()
        return True
    except Exception as e:
        print(f"[ERROR] upsert failed for {row['id']}: {e}", flush=True)
        return False
```

### ARGPARSE_PATTERN
```python
# SOURCE: scraper.py:46-53
def parse_args():
    parser = argparse.ArgumentParser(description="Reddit mention scraper")
    parser.add_argument("--dry-run", action="store_true", help="...")
    return parser.parse_args()
```

### SUMMARY_FOOTER
```python
# SOURCE: scraper.py:234-240
suffix = " (dry-run)" if args.dry_run else ""
print(
    f"\n[SUMMARY] Terms: {len(terms)} | Subreddits: {len(SUBREDDITS)} |"
    f" Requests: {total_requests} | Rows upserted: {total_upserted}{suffix}",
    flush=True,
)
```

### COMPETITOR_MATCH
```python
# SOURCE: scraper.py:115-119
def mentions_competitor(body: str, terms: list) -> bool:
    return any(
        re.search(r"\b" + re.escape(term) + r"\b", body, re.IGNORECASE)
        for term in terms
    )
# Use same pattern for sentence-level filtering
```

### SUPABASE_INSERT
```python
# SOURCE: CLAUDE.md Supabase section + schema.sql
# Insert (not upsert) — UUID PK is auto-generated
db.table("mention_analyses").insert(rows).execute()
# rows is a list[dict] matching schema column names exactly
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `analyzer.py` | CREATE | New pipeline — all logic in single file matching scraper.py style |
| `requirements.txt` | UPDATE | Add `torch` CPU install note; verify `anthropic`, `transformers` present |

## NOT Building

- Comment-thread analysis (scraper v1 scope limit)
- Sentiment inside Haiku prompt (cost rule)
- Parallel Haiku batches (rate-limit safety)
- Case-sensitive pre-filtering (too strict)
- Dashboard UI changes
- Cron scheduling (manual-run only)
- Per-mention retry on RoBERTa (quotes are short; OOM is not expected)

---

## Constants Block

Place at module top, after imports and `load_dotenv()`:

```python
BATCH_SIZE = 25
MAX_QUOTE_WORDS = 50
MAX_WINDOW_CHARS = 800
SENTENCE_PRE_WINDOW = 1   # sentences before hit
SENTENCE_POST_WINDOW = 2  # sentences after hit
MAX_ROBERTA_TOKENS = 128
MODEL = "claude-haiku-4-5"
ROBERTA_MODEL = "cardiffnlp/twitter-roberta-base-topic-sentiment-latest"
HAIKU_INPUT_COST_PER_M = 0.80   # USD per million tokens
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
    "project budgeting", "payroll integration", "GPS tracking", "geofencing",
    "calendar sync", "Jira integration", "Asana integration", "Slack integration",
    "QuickBooks integration", "Xero integration", "API access", "data export",
    "excel export", "Zapier integration", "project tool integrations",
    "accounting integrations", "ease of use", "onboarding", "customer support",
    "UI design", "pricing", "free tier", "privacy concerns",
    "surveillance concerns", "accuracy", "reliability",
]
```

---

## Step-by-Step Tasks

### Task 1: Imports, env loading, and module-level pipeline init

- **ACTION**: Write the top of `analyzer.py` — imports, `load_dotenv()`, env vars, constants, and cardiffnlp pipeline loaded once into a module-level variable.
- **IMPLEMENT**:
```python
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

# --- constants (see Constants Block above) ---

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Load RoBERTa once at module level — never re-instantiate per batch
print(f"[INFO] Loading model: {ROBERTA_MODEL}", flush=True)
pipe = hf_pipeline("text-classification", model=ROBERTA_MODEL)
print(f"[INFO] Model loaded", flush=True)
```
- **MIRROR**: ENV_LOADING, NAMING_CONVENTION
- **IMPORTS**: `anthropic`, `transformers.pipeline`, `supabase.create_client`
- **GOTCHA**: `pipe` must be module-level, not inside any function. If it were inside `main()`, it would re-load on every call in tests. Import `pipeline` as `hf_pipeline` to avoid shadowing the local variable name `pipe`.
- **VALIDATE**: `python analyzer.py --dry-run` prints "Model loaded" without error.

---

### Task 2: `parse_args()`

- **ACTION**: Define argument parser with `--dry-run` and `--limit`.
- **IMPLEMENT**:
```python
def parse_args():
    parser = argparse.ArgumentParser(description="Mention analyzer: Haiku extraction + RoBERTa scoring")
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
```
- **MIRROR**: ARGPARSE_PATTERN
- **GOTCHA**: `--dry-run` semantics here differ from `scraper.py` — it exits after Layer 1, not after the full pipeline. Document this in the help string.
- **VALIDATE**: `python analyzer.py --help` shows both flags.

---

### Task 3: `build_skip_set(db)` — fetch already-analyzed mention_ids

- **ACTION**: Query `mention_analyses` once at startup for all existing `mention_id` values.
- **IMPLEMENT**:
```python
def build_skip_set(db) -> set[str]:
    resp = db.table("mention_analyses").select("mention_id").execute()
    ids = {r["mention_id"] for r in resp.data}
    print(f"[INFO] Skip set built: {len(ids)} already-analyzed mention_ids", flush=True)
    return ids
```
- **MIRROR**: LOGGING_PATTERN, SUPABASE_INSERT (read pattern)
- **GOTCHA**: Use a `set` not a `list` — O(1) membership check matters when filtering 350 rows.
- **VALIDATE**: Returns empty set on a fresh DB; returns non-empty set after a prior analyzer run.

---

### Task 4: `fetch_unanalyzed(db, skip_set, limit)` — load raw_mentions

- **ACTION**: Fetch all `raw_mentions` rows not in the skip set; apply `--limit` if set.
- **IMPLEMENT**:
```python
def fetch_unanalyzed(db, skip_set: set[str], limit: Optional[int]) -> list[dict]:
    resp = db.table("raw_mentions").select("id, body, subreddit, url").execute()
    rows = [r for r in resp.data if r["id"] not in skip_set]
    if limit:
        rows = rows[:limit]
    print(f"[INFO] {len(rows)} unanalyzed mentions after skip-set filter", flush=True)
    return rows
```
- **MIRROR**: LOGGING_PATTERN
- **GOTCHA**: Supabase free tier returns max 1000 rows per select by default. At 350 rows this is fine for v1. If volume grows past 1000, add `.range(0, 9999)` or paginate.
- **VALIDATE**: Returns 0 rows if all mentions are already analyzed.

---

### Task 5: `filter_by_competitor(rows)` — case-insensitive string-match pre-filter

- **ACTION**: Keep only rows whose `body` contains at least one competitor name (word-boundary match, case-insensitive).
- **IMPLEMENT**:
```python
def filter_by_competitor(rows: list[dict]) -> list[dict]:
    matched = [r for r in rows if _mentions_any_competitor(r["body"])]
    print(
        f"[INFO] {len(matched)}/{len(rows)} mentions pass competitor string-match",
        flush=True,
    )
    return matched

def _mentions_any_competitor(body: str) -> bool:
    return any(
        re.search(r"\b" + re.escape(c) + r"\b", body, re.IGNORECASE)
        for c in COMPETITORS
    )
```
- **MIRROR**: COMPETITOR_MATCH
- **GOTCHA**: `re.escape` handles `Reclaim.ai` (dot is a regex metacharacter). The word-boundary `\b` won't match mid-word but works for all 18 competitor names as written.
- **VALIDATE**: "Aliens have come to harvest Human DNA" — passes `_mentions_any_competitor` because "harvest" matches `\bHarvest\b` case-insensitively. This false positive reaches Haiku, where the relevance gate returns `[]`. Correct behavior.

---

### Task 6: `extract_window(body, competitor_names)` — ±sentence window around keyword hits

- **ACTION**: Split body into sentences; find sentences containing any competitor name; extract `SENTENCE_PRE_WINDOW` before and `SENTENCE_POST_WINDOW` after each hit; merge overlapping windows; cap at `MAX_WINDOW_CHARS`.
- **IMPLEMENT**:
```python
def extract_window(body: str) -> str:
    sentences = re.split(r'(?<=[.!?])\s+', body.strip())
    hit_indices = [
        i for i, s in enumerate(sentences)
        if _mentions_any_competitor(s)
    ]
    if not hit_indices:
        # Fallback: competitor matched in body but no individual sentence matched
        # (e.g. keyword split across sentence boundary — rare)
        print("[WARN] No competitor-matching sentences found; using full body", flush=True)
        return body[:MAX_WINDOW_CHARS]

    # Build index ranges, merge overlaps
    ranges = [
        (max(0, i - SENTENCE_PRE_WINDOW), min(len(sentences) - 1, i + SENTENCE_POST_WINDOW))
        for i in hit_indices
    ]
    merged = _merge_ranges(ranges)

    window_sentences = []
    for start, end in merged:
        window_sentences.extend(sentences[start:end + 1])

    window = " ".join(window_sentences)
    return window[:MAX_WINDOW_CHARS]

def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    sorted_ranges = sorted(ranges)
    merged = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
```
- **MIRROR**: COMPETITOR_MATCH (reuses `_mentions_any_competitor`)
- **GOTCHA**: `re.split(r'(?<=[.!?])\s+', body)` splits on `.`, `!`, `?` followed by whitespace. Abbreviations like "vs." may split mid-sentence — acceptable noise for v1. The `SENTENCE_POST_WINDOW = 2` captures the trailing analysis lines in the Toggl Track example.
- **VALIDATE**: For the Toggl Track example body, the windowed output should include both the "Toggl Track – A close second..." sentence and the following analysis sentence.

---

### Task 7: `build_haiku_prompt(batch)` — extraction-only system prompt

- **ACTION**: Construct the system prompt and user message for a batch of 25 windowed excerpts.
- **IMPLEMENT**:
```python
SYSTEM_PROMPT = f"""You extract structured data from Reddit text about time-tracking tools.
Competitors: {", ".join(COMPETITORS)}
Features: {", ".join(FEATURES)}
Return ONLY a valid JSON array, no preamble, no markdown fences, no explanation.
Each element: {{"mention_id": str, "competitor": str, "feature": str, "supporting_quote": str}}
supporting_quote: ≤{MAX_QUOTE_WORDS} words, direct excerpt or close paraphrase from the text.
Only extract if the competitor name clearly refers to the time-tracking software product.
If the name appears in an unrelated context (e.g. "harvest" as a verb, unrelated brand), return [] for that mention.
Return [] if no signal found."""

def build_user_message(batch: list[dict]) -> str:
    lines = []
    for item in batch:
        lines.append(f'[{item["mention_id"]}] {item["window"]}')
    return "\n\n".join(lines)
```
- **MIRROR**: NAMING_CONVENTION (module-level constant for the system prompt)
- **GOTCHA**: `SYSTEM_PROMPT` is a module-level constant built once (COMPETITORS and FEATURES are known at import time). Do not rebuild it per batch — no dynamic parts. The `{{"mention_id": ...}}` double-braces are needed inside an f-string to produce literal `{`.
- **VALIDATE**: `SYSTEM_PROMPT` contains all 18 competitor names and the relevance gate instruction.

---

### Task 8: `call_haiku(client, batch)` — Haiku API call with retry on malformed JSON

- **ACTION**: Send one batch to Haiku; parse JSON; strip markdown fences defensively; retry once on `json.JSONDecodeError`; log tokens and cost.
- **IMPLEMENT**:
```python
def call_haiku(client: anthropic.Anthropic, batch: list[dict]) -> list[dict]:
    user_msg = build_user_message(batch)

    for attempt in range(1, 3):  # max 2 attempts
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
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
            # Log cost after successful parse
            in_t = response.usage.input_tokens
            out_t = response.usage.output_tokens
            cost = (in_t * HAIKU_INPUT_COST_PER_M + out_t * HAIKU_OUTPUT_COST_PER_M) / 1_000_000
            print(
                f"[INFO] Haiku tokens in={in_t} out={out_t} | est ${cost:.4f}",
                flush=True,
            )
            return rows if isinstance(rows, list) else []
        except json.JSONDecodeError:
            print(
                f"[WARN] Malformed JSON from Haiku (attempt {attempt}/2) — "
                + ("retrying" if attempt == 1 else "skipping batch"),
                flush=True,
            )

    return []  # skip batch after 2 failures
```
- **MIRROR**: LOGGING_PATTERN, retry pattern from `search_page()` (scraper.py:99-112)
- **GOTCHA**: Log cost ONLY after a successful `json.loads()` — don't log for failed attempts. `response.usage` is always present on a successful API response. `max_tokens=2048` is sufficient for 25 short JSON elements; Haiku's output per batch will rarely exceed 500 tokens.
- **VALIDATE**: Inject a known-bad response (mock `raw = "not json"`) and verify `[WARN] Malformed JSON` appears twice, then returns `[]`.

---

### Task 9: `score_quote(quote)` — RoBERTa inference

- **ACTION**: Score a single `supporting_quote` using the module-level `pipe`; return `(label, score)`.
- **IMPLEMENT**:
```python
def score_quote(quote: str) -> tuple[str, float]:
    result = pipe(quote, truncation=True, max_length=MAX_ROBERTA_TOKENS)[0]
    return result["label"], round(result["score"], 2)
```
- **MIRROR**: NAMING_CONVENTION (small focused function)
- **GOTCHA**: `pipe(...)` returns a list — always index `[0]`. `round(..., 2)` matches `numeric(3,2)` schema type. Do not pass an empty string — check upstream that `supporting_quote` is non-empty before calling.
- **VALIDATE**: `score_quote("Clockify is great for freelancers")` returns a label from the five canonical strings and a score in [0.0, 1.0].

---

### Task 10: `insert_batch(db, rows, dry_run)` — upsert or dry-run print

- **ACTION**: Insert a list of `mention_analyses` dicts; dry-run prints instead of inserting.
- **IMPLEMENT**:
```python
def insert_batch(db, rows: list[dict], dry_run: bool) -> int:
    if not rows:
        return 0
    if dry_run:
        for r in rows:
            print(
                f"[DRY RUN] mention_id={r['mention_id']} competitor={r['competitor']}"
                f" feature={r['feature']} quote=\"{r['supporting_quote'][:60]}...\"",
                flush=True,
            )
        return len(rows)
    try:
        db.table("mention_analyses").insert(rows).execute()
        return len(rows)
    except Exception as e:
        print(f"[ERROR] insert_batch failed: {e}", flush=True)
        return 0
```
- **MIRROR**: DRY_RUN_PATTERN, SUPABASE_INSERT
- **GOTCHA**: Use `insert()` not `upsert()` — `mention_analyses` PK is a generated UUID, there is no natural dedup key. A re-run on already-analyzed mentions is prevented by the skip set built at startup, not by `on_conflict`.
- **VALIDATE**: Dry-run prints rows without touching DB; live run increases row count in `mention_analyses`.

---

### Task 11: `main()` — orchestration

- **ACTION**: Wire all functions into the pipeline loop.
- **IMPLEMENT**:
```python
def main():
    args = parse_args()
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    skip_set = build_skip_set(db)
    mentions = fetch_unanalyzed(db, skip_set, args.limit)
    mentions = filter_by_competitor(mentions)

    if not mentions:
        print("[INFO] No mentions to analyze — exiting", flush=True)
        return

    # Pre-compute sentence windows
    for m in mentions:
        m["window"] = extract_window(m["body"])

    # Batch loop
    batches = [mentions[i:i + BATCH_SIZE] for i in range(0, len(mentions), BATCH_SIZE)]
    total_batches = len(batches)
    total_rows_written = 0
    total_batches_skipped = 0

    for batch_num, batch in enumerate(batches, start=1):
        print(f"[INFO] Batch {batch_num}/{total_batches} — sending {len(batch)} mentions to Haiku", flush=True)

        extracted = call_haiku(client, batch)

        if not extracted:
            print(f"[WARN] Batch {batch_num} produced no rows (malformed JSON or all [])", flush=True)
            total_batches_skipped += 1

        if args.dry_run:
            # Print extracted rows and continue — no RoBERTa, no upsert
            for r in extracted:
                print(
                    f"[DRY RUN] mention_id={r.get('mention_id')} competitor={r.get('competitor')}"
                    f" feature={r.get('feature')} quote=\"{str(r.get('supporting_quote',''))[:60]}\"",
                    flush=True,
                )
            continue  # next batch

        # Layer 2: score each extracted row
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
        print(f"[INFO] Batch {batch_num}/{total_batches} — {written} rows written", flush=True)

    suffix = " (dry-run: Layer 1 only)" if args.dry_run else ""
    print(
        f"\n[SUMMARY] Batches: {total_batches} | Rows written: {total_rows_written}"
        f" | Batches skipped: {total_batches_skipped}{suffix}",
        flush=True,
    )


if __name__ == "__main__":
    main()
```
- **MIRROR**: SUMMARY_FOOTER, DRY_RUN_PATTERN, LOGGING_PATTERN
- **GOTCHA**: In dry-run mode, `continue` skips RoBERTa and upsert but still increments `batch_num` and prints the Haiku extraction. `total_rows_written` stays 0 in dry-run — that's correct and expected. The `[SUMMARY]` suffix makes this explicit.
- **VALIDATE**: Dry-run exits without modifying DB; full run adds rows to `mention_analyses`; both print the `[SUMMARY]` line.

---

### Task 12: Update `requirements.txt`

- **ACTION**: Add `torch` CPU note; confirm all other deps present.
- **IMPLEMENT**:
```
requests>=2.31.0
supabase>=2.0.0
python-dotenv>=1.0.0
anthropic>=0.25.0
transformers>=4.40.0
# torch CPU: pip install torch --index-url https://download.pytorch.org/whl/cpu
```
- **GOTCHA**: `torch` is not listed as a normal pip dependency because it requires the CPU-specific index URL. The comment preserves the install instruction without breaking `pip install -r requirements.txt`.
- **VALIDATE**: `pip install -r requirements.txt` completes without error (assuming torch already installed via its own command).

---

## Testing Strategy

### Manual Validation (primary for v1)

| Test | How | Expected |
|---|---|---|
| Dry-run with known data | `python analyzer.py --dry-run --limit 5` | Prints Haiku extractions; no DB change |
| Irrelevant keyword (harvest) | Inspect dry-run output for mention containing "harvest" in non-tool context | Row absent or marked `[]` in log |
| Trailing context captured | Inspect dry-run output for Toggl Track–style post | Quote includes trailing analysis, not just the header line |
| Full run | `python analyzer.py --limit 10` | 10 rows appear in `mention_analyses` |
| Skip set | Run full pipeline twice on same data | Second run: "0 unanalyzed mentions" — no duplicate rows |
| Cost logging | Any run | `[INFO] Haiku tokens in=N out=M | est $X.XXXX` per batch |
| Malformed JSON | N/A — not easily injectable in manual test; verified by code review | |

### Edge Cases Checklist
- [ ] All mentions already analyzed → exits cleanly with "No mentions to analyze"
- [ ] Empty `raw_mentions` table → exits cleanly
- [ ] Haiku returns `[]` for all items in a batch → `total_batches_skipped` incremented; no crash
- [ ] `supporting_quote` is empty string from Haiku → skipped before `score_quote()` call
- [ ] `--limit 0` → processes 0 mentions (Python slicing `[:0]` returns empty list)
- [ ] Missing env var → `KeyError` at module load with clear key name in traceback

---

## Validation Commands

### Static Analysis
```bash
cd /Users/devikulkarni/Documents/claudetest/Vibecheck
python -m py_compile analyzer.py
```
EXPECT: No output (clean compile)

### Dry-Run (no API cost, no DB writes)
```bash
python analyzer.py --dry-run --limit 5
```
EXPECT: `[INFO] Model loaded`, Haiku extraction output for up to 5 mentions, `[SUMMARY]` line with `dry-run: Layer 1 only`

### Full Run (live)
```bash
python analyzer.py --limit 10
```
EXPECT: 10 rows in `mention_analyses` after run; `[SUMMARY]` shows `Rows written: ≤10`

### Skip Set Verification
```bash
python analyzer.py --dry-run --limit 5
# Then:
python analyzer.py --dry-run
```
EXPECT: Second run's "unanalyzed mentions after skip-set filter" count is unchanged (dry-run doesn't write).

### Full Dataset Run
```bash
python analyzer.py
```
EXPECT: All ~312 competitor-matching mentions processed; `[SUMMARY]` shows expected row count.

---

## Acceptance Criteria

- [ ] `python analyzer.py --dry-run` prints Haiku extractions and exits without touching DB or loading RoBERTa scoring path unnecessarily *(model still loads at module level — this is correct)*
- [ ] `python analyzer.py` populates `mention_analyses` with `sentiment_label` and `sentiment_score` for each extracted row
- [ ] Already-analyzed `mention_ids` are never re-processed
- [ ] "harvest Human DNA" example produces no row in `mention_analyses`
- [ ] Trailing analysis sentences are included in windowed excerpts (Toggl Track example)
- [ ] Cost logged per batch in format `est $X.XXXX`
- [ ] Malformed Haiku JSON: retried once, then batch skipped with `[WARN]`
- [ ] `[SUMMARY]` printed at end of every run (dry-run or live)

## Completion Checklist

- [ ] Code follows `scraper.py` conventions exactly (logging format, dry-run pattern, summary footer)
- [ ] `pipe` is module-level — never re-instantiated per batch or per call
- [ ] `SYSTEM_PROMPT` is module-level — never rebuilt per batch
- [ ] Markdown fence stripping applied before every `json.loads()`
- [ ] `insert()` used (not `upsert()`) for `mention_analyses`
- [ ] `re.escape()` used for all competitor name regex patterns
- [ ] No hardcoded strings outside the constants block
- [ ] All `print()` calls use `flush=True` and `[LEVEL]` prefix

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Haiku wraps JSON in markdown fences | M | Batch silently skipped | Defensive fence-stripping before `json.loads()` |
| Sentence regex splits on abbreviation (e.g. "e.g.") | L | Window boundary shifts by 1 sentence | Acceptable noise; quote content unaffected |
| Supabase returns >1000 rows when dataset grows | L | Unanalyzed mentions silently truncated | Add `.range()` pagination in Task 4 when volume exceeds 1000 |
| `pipe` model not cached locally (first run) | M | Cold start takes 2–5 min to download | GitHub Actions caches `~/.cache/huggingface` between runs per CLAUDE.md |

## Notes

- `torch` must be installed separately via `pip install torch --index-url https://download.pytorch.org/whl/cpu` before `analyzer.py` runs. The `requirements.txt` comment preserves this instruction.
- The `COMPETITORS` constant in `analyzer.py` must stay in sync with `search_terms` table seed data in `schema.sql`. If a competitor is added to the DB, update the constant too.
- `sentiment_score` from cardiffnlp is the model's **confidence in its predicted label** — not a positive/negative polarity axis. A `strongly negative` label with score 0.97 means the model is 97% confident it is strongly negative. This is the correct interpretation per `Documentation-Roberta.md`.
