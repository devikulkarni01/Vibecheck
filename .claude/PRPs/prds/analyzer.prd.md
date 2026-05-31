# analyzer.py — Two-Layer Mention Analysis Pipeline

## Problem Statement

Raw Reddit mentions collected by `scraper.py` sit unanalyzed in `raw_mentions`. Without a structured extraction and sentiment-scoring pass, the dashboard heatmap, pain-points view, and opportunity matrix have no data to display. The analysis pipeline bridges raw text and actionable competitive intelligence.

## Evidence

- `raw_mentions` table is populated by `scraper.py` but `mention_analyses` is empty until `analyzer.py` runs.
- Dashboard tabs (Heatmap, Pain Points, Opportunity Matrix) all read from `mention_analyses`; without rows there, every tab is blank.
- CLAUDE.md specifies the two-layer architecture, batch size (25), model choice, and cost rules — design decisions are already validated by project owners.
- 350 existing `raw_mentions` rows total ~831K chars (~2,374 chars avg per body); full-body sending is safe for context window and cost at this volume.

## Proposed Solution

A two-layer pipeline in `analyzer.py`:

1. **Layer 1 — Haiku extraction**: for each mention, extract a ±2-sentence window around each competitor keyword hit and send those windowed excerpts (not the full body) to Haiku in batches of 25. Haiku returns `(mention_id, competitor, feature, supporting_quote)` only — no sentiment in prompt. Acts as a relevance gate: returns `[]` for mentions where the competitor name is not used in a tool context.
2. **Layer 2 — cardiffnlp RoBERTa**: run local inference on each `supporting_quote` (≤50 words); emit `sentiment_label` + `sentiment_score`. Pipeline loaded once at startup, reused across all batches.

Results are upserted into `mention_analyses`. `--dry-run` exits after Layer 1, printing extractions for operator review. Mentions without a case-insensitive competitor string-match are skipped before any API call; already-analyzed `mention_ids` are also skipped.

## Key Hypothesis

We believe a two-layer extraction + local-scoring pipeline with keyword-windowed context and Haiku relevance gating will convert raw Reddit text into structured competitive signals for the dashboard with predictable cost and no per-mention API overhead for sentiment.
We'll know we're right when `mention_analyses` is populated after a full run, the Heatmap tab renders non-empty cells with meaningful sentiment variance, and irrelevant keyword matches (e.g. "harvest" as a verb) produce no rows.

## What We're NOT Building

- Comment-thread fetching — scraper.py v1 already deferred this; analyzer.py inherits the same scope.
- Sentiment scoring inside the Haiku prompt — explicitly prohibited by cost rules.
- Any dashboard UI changes — analyzer.py is a backend pipeline only.
- Scheduling / cron triggering — v1 is manual-run only (GitHub Actions `workflow_dispatch`).
- Case-sensitive pre-filtering — too strict; drops valid lowercase mentions (e.g. "clockify").

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| Rows written per run | ≥ 80% of unanalyzed raw_mentions with ≥1 competitor match | COUNT before/after run |
| Haiku token cost per batch | < $0.01 per 25-mention batch | Logged token usage |
| Pipeline runtime (350 mentions) | < 5 min end-to-end | Wall-clock log |
| Malformed-JSON skip rate | < 5% of batches | Log output |
| False-positive rows (irrelevant competitor name usage) | Near zero | Manual spot-check of dry-run output |

## Open Questions

- [ ] If no competitor-matching sentences are found after windowing (edge case: keyword matched on title, not body), fall back to full body or skip entirely?
- [ ] What retry strategy applies when Supabase upsert fails (network error vs. constraint violation)?

---

## Users & Context

**Primary User**
- **Who**: The analyst/operator running the tool manually or via GitHub Actions
- **Current behavior**: Runs `scraper.py`, then has no automated way to analyze results
- **Trigger**: After a scrape run completes, or on demand
- **Success state**: `mention_analyses` populated; dashboard tabs show data

**Job to Be Done**
When I've collected fresh Reddit mentions, I want to extract structured competitor/feature signals and sentiment scores automatically, so I can see the competitive landscape without reading raw posts manually.

**Non-Users**
End users of the dashboard — they consume the output, not the pipeline.

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | Load env vars (SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY) at startup; fail fast if missing | Script is unusable without them |
| Must | Load cardiffnlp pipeline once at startup, reuse across all batches — never re-instantiate per batch | Cost rule #7 |
| Must | Case-insensitive string-match pre-filter: skip mentions with no competitor name before any API call | Cost rule #1 |
| Must | Skip already-analyzed mention_ids (query mention_analyses at startup, build skip set) | Cost rule #2 |
| Must | ±2 sentence window extraction around each competitor keyword hit per body | Signal quality; avoids sending irrelevant preamble |
| Must | Batch exactly 25 windowed excerpts per Haiku call | Cost rule #3 |
| Must | Haiku prompt returns only extraction JSON array — no scoring verbiage, no preamble | Cost rule #5; prevents JSON parse failures |
| Must | Haiku acts as relevance gate: returns `[]` if competitor name is not used in a tool context | False-positive suppression |
| Must | Quote cap in Haiku prompt: supporting_quote ≤ 50 words | Enough context for RoBERTa; bounded token cost |
| Must | Retry once on malformed Haiku JSON (`json.JSONDecodeError`), then skip batch and log | Robustness |
| Must | `--dry-run` exits after Layer 1, prints extracted rows to stdout — no RoBERTa call, no upsert | Operator review checkpoint before scoring |
| Must | Layer 2: `pipe(quote, truncation=True, max_length=128)` — HuggingFace handles token overflow natively | CLAUDE.md spec |
| Must | Upsert results into mention_analyses via service role key | CLAUDE.md spec |
| Must | Log token usage + estimated cost after each batch | Cost rule #6 |
| Should | Progress logging per batch (batch N of M, rows written) | Operator visibility |
| Should | Graceful handling of empty Haiku response (`[]`) without crashing | Robustness |
| Could | `--limit N` flag to cap mentions processed in one run | Useful for testing |
| Won't | GUI or interactive mode | CLI pipeline only |
| Won't | Parallel Haiku calls | Rate limit safety; sequential sufficient for v1 |

### MVP Scope

Single-file `analyzer.py`: load model → fetch unanalyzed mentions → pre-filter → window extraction → batch → Haiku extract (with relevance gate) → `--dry-run` exit or RoBERTa score → upsert.

### User Flow

```
python analyzer.py [--dry-run] [--limit N]
  → load cardiffnlp pipeline once (log "Model loaded")
  → fetch all mention_ids already in mention_analyses → build skip set
  → fetch raw_mentions rows not in skip set
  → case-insensitive string-match filter: keep rows with ≥1 competitor name in body
  → for each mention body:
      → split into sentences
      → find sentences containing ≥1 competitor name
      → for each hit: extract sentence[-1], sentence[0] (hit), sentence[+1], sentence[+2]
      → merge overlapping windows; cap total windowed text at ~800 chars
      → if no sentences found: fall back to full body (edge case)
  → chunk windowed excerpts into batches of 25
  → for each batch:
      → call Haiku with extraction-only prompt → parse JSON array
      → on json.JSONDecodeError: retry once with same payload
      → on second failure: skip batch, log warning, continue
      → log input_tokens, output_tokens, estimated cost
      → [--dry-run: print extracted rows, continue to next batch, exit after all batches]
      → for each row in JSON array:
          → pipe(supporting_quote, truncation=True, max_length=128)
          → assemble mention_analyses row (mention_id, competitor, feature,
            sentiment_score, sentiment_label, supporting_quote)
      → upsert batch to mention_analyses
      → log rows written for this batch
  → print summary: batches processed, total rows written, batches skipped
```

### Haiku Prompt Structure

```
SYSTEM:
You extract structured data from Reddit text about time-tracking tools.
Competitors: {COMPETITOR_LIST}
Features: {FEATURE_LIST}
Return ONLY a valid JSON array, no preamble, no markdown fences, no explanation.
Each element: {"mention_id": str, "competitor": str, "feature": str, "supporting_quote": str}
supporting_quote: ≤50 words, must be a direct excerpt or close paraphrase from the text.
Only extract if the competitor name clearly refers to the time-tracking software product.
If the name appears in an unrelated context, return [] for that mention.
Return [] if no signal found.

USER:
{list of 25 windowed excerpts, each prefixed with mention_id}
```

---

## Technical Approach

**Feasibility**: HIGH — all dependencies specified in CLAUDE.md; scraper.py provides env-loading, Supabase-client, and `--dry-run` patterns to mirror.

**Architecture Notes**
- cardiffnlp pipeline assigned to a module-level variable at startup — single load, reused for every `pipe()` call throughout the process lifetime.
- Sentence splitting: `re.split(r'(?<=[.!?])\s+', body)` — simple regex, no NLTK dependency.
- Window merge: collect all (start, end) sentence index ranges for each hit; merge overlapping ranges; join selected sentences. Cap joined text at 800 chars before sending to Haiku.
- Haiku cost estimate per batch: `(input_tokens * 0.80 + output_tokens * 4.00) / 1_000_000` (USD).
- Supabase upsert: single `supabase.table("mention_analyses").insert(rows).execute()` per batch — no `on_conflict` needed since `id` is a generated UUID.

**Key constants**
```python
BATCH_SIZE = 25
MAX_QUOTE_WORDS = 50
MAX_WINDOW_CHARS = 800
SENTENCE_WINDOW = 2          # sentences after the hit sentence
SENTENCE_PRE_WINDOW = 1      # sentences before the hit sentence
MAX_ROBERTA_TOKENS = 128
MODEL = "claude-haiku-4-5"
ROBERTA_MODEL = "cardiffnlp/twitter-roberta-base-topic-sentiment-latest"
HAIKU_INPUT_COST_PER_M = 0.80
HAIKU_OUTPUT_COST_PER_M = 4.00
```

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Haiku returns malformed JSON | M | Retry once; skip batch on second failure; log warning |
| Haiku returns JSON with markdown fences (```json ... ```) | M | Strip fences before `json.loads()` as a defensive step |
| Sentence regex splits mid-abbreviation (e.g. "vs.") | L | Acceptable noise for v1; affects window boundaries, not correctness |
| No competitor-matching sentences found after windowing | L | Fall back to full body; log fallback occurred |
| RoBERTa OOM on CPU | L | 128-token truncation; quotes are short by design |
| Duplicate rows from a re-run of the same batch | L | Skip set built at startup prevents re-processing already-analyzed mention_ids |

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | Bootstrap | Env loading, Supabase client, cardiffnlp pipeline init (once), arg parsing | complete | - | - | `.claude/PRPs/plans/completed/analyzer.plan.md` |
| 2 | Data fetch & pre-filter | Fetch unanalyzed raw_mentions, build skip set, apply competitor string-match | complete | - | 1 | `.claude/PRPs/plans/completed/analyzer.plan.md` |
| 3 | Sentence windowing | Split bodies into sentences, extract ±window around keyword hits, cap at 800 chars | complete | - | 2 | `.claude/PRPs/plans/completed/analyzer.plan.md` |
| 4 | Layer 1 — Haiku extraction | Batch loop, prompt construction (extraction-only JSON), parse + retry + cost logging | complete | - | 3 | `.claude/PRPs/plans/completed/analyzer.plan.md` |
| 5 | Layer 2 — RoBERTa scoring + upsert | Score each quote, assemble rows, upsert (or dry-run print), summary logging | complete | - | 4 | `.clone/PRPs/plans/completed/analyzer.plan.md` |

### Phase Details

**Phase 1: Bootstrap**
- **Goal**: Script starts cleanly; crashes fast on missing env vars; cardiffnlp pipeline loaded exactly once.
- **Scope**: `load_dotenv()`, `os.environ[...]` for SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY. `create_client(...)`. `pipeline(ROBERTA_MODEL)` assigned to module-level `pipe`. `argparse` with `--dry-run`, `--limit`.
- **Success signal**: `python analyzer.py --dry-run` logs "Model loaded" and exits cleanly when no unanalyzed mentions exist.

**Phase 2: Data fetch & pre-filter**
- **Goal**: Identify working set of mentions to analyze.
- **Scope**: Query `mention_analyses` for distinct `mention_id` values → `skip_set`. Query `raw_mentions` selecting `id, body, subreddit, url`. Filter out `skip_set`. Case-insensitive string-match against COMPETITORS list. Apply `--limit` if set.
- **Success signal**: Correct count logged; zero API calls when all mentions already analyzed.

**Phase 3: Sentence windowing**
- **Goal**: Reduce each body to competitor-relevant sentences only, preserving trailing context.
- **Scope**: `re.split(r'(?<=[.!?])\s+', body)` → sentence list. Find indices of sentences containing any competitor name (case-insensitive). For each hit index `i`: collect sentences `[i-1 .. i+2]` (clamped to list bounds). Merge overlapping ranges. Join and cap at 800 chars. If no hits found: use full body, log fallback.
- **Success signal**: A post like the Toggl Track example produces a window that includes the trailing analysis sentences, not just the header line.

**Phase 4: Layer 1 — Haiku extraction**
- **Goal**: Extract structured (competitor, feature, quote) tuples; gate out irrelevant mentions.
- **Scope**: Build system prompt with COMPETITORS and FEATURES lists. User message: 25 windowed excerpts each prefixed `[{mention_id}]`. Call `anthropic_client.messages.create(model=MODEL, ...)`. Strip markdown fences defensively before `json.loads()`. On `json.JSONDecodeError`: retry once with identical payload. On second failure: log and skip batch. Log `input_tokens`, `output_tokens`, estimated USD cost. If `--dry-run`: print extracted rows as JSON and continue to next batch; exit with summary after all batches.
- **Success signal**: dry-run output shows `[]` for the "harvest Human DNA" mention; shows valid tuples for genuine tool mentions.

**Phase 5: Layer 2 — RoBERTa scoring + upsert**
- **Goal**: Score each quote and persist to `mention_analyses`.
- **Scope**: For each extracted row: `result = pipe(supporting_quote, truncation=True, max_length=MAX_ROBERTA_TOKENS)[0]`. Assemble dict: `{mention_id, competitor, feature, sentiment_label: result['label'], sentiment_score: round(result['score'], 2), supporting_quote}`. Collect all rows for the batch. `supabase.table("mention_analyses").insert(rows).execute()`. Log rows written. After all batches: print summary (batches processed, total rows written, batches skipped).
- **Success signal**: Row count in `mention_analyses` increases by expected amount; `sentiment_score` values are in [0.0, 1.0]; labels are one of the five canonical strings.

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| Sentiment in prompt | No — Layer 2 only | Single Haiku call with sentiment | Cost rule; RoBERTa is free and deterministic |
| Batch size | 25 | 10, 50, 100 | CLAUDE.md spec |
| Model | claude-haiku-4-5 | Sonnet, Opus | Cost rule #4 |
| Skip set source | Query mention_analyses at startup | Per-batch check | Single query vs N per-batch checks |
| Competitor list source | Hardcoded constant | DB read from search_terms | Avoids round-trip; prompt needs stable list |
| Body pre-processing | ±2 sentence window around keyword hits | Head truncation, full body | Head truncation misses buried mentions; full body sends noise |
| Window cap | 800 chars | 500, 1000, none | Bounds batch token cost; avg body is 2,374 chars so this is ~33% |
| Quote cap | ≤50 words | ≤20 words, ≤100 words | 20 words cuts trailing analysis; 50 words gives RoBERTa sufficient context |
| RoBERTa truncation | `truncation=True, max_length=128` natively | Character pre-truncation | Quotes ≤50 words rarely exceed 128 tokens; HuggingFace handles overflow natively |
| Case sensitivity | Case-insensitive throughout | Case-sensitive | Case-sensitive too strict; drops valid lowercase mentions |
| `--dry-run` semantics | Exit after Layer 1, print Haiku extractions | Full run without upsert | Layer 1 output is the right checkpoint — operator reviews relevance before RoBERTa runs |
| Haiku relevance gate | Return `[]` for non-tool competitor name usage | Post-hoc filtering | Cheapest to gate at Haiku; no RoBERTa call wasted on irrelevant rows |
| Markdown fence stripping | Defensive strip before json.loads() | Trust Haiku to return clean JSON | Haiku occasionally wraps output in ```json fences despite instructions |
| Insert vs upsert | `insert()` (no on_conflict) | upsert with on_conflict | mention_analyses PK is a generated UUID; no natural dedup key needed |

---

## Research Summary

**Technical Context**
- `scraper.py` establishes env-loading, Supabase-client, and `--dry-run` patterns that `analyzer.py` mirrors.
- `schema.sql`: `mention_analyses` has no unique constraint on `(mention_id, competitor, feature)` — multiple rows per mention are expected and correct.
- cardiffnlp: five labels (`strongly negative`, `negative`, `negative or neutral`, `positive`, `strongly positive`); `sentiment_score` is model confidence in the predicted label, not a polarity axis.
- 350 `raw_mentions` rows, ~831K chars total (~2,374 avg). Full-body sending is safe at this volume but windowing improves signal quality regardless of cost.

---

*Generated: 2026-05-31*
*Status: UPDATED — proceeding to /prp-plan*
