# Vibecheck Dashboard — Competitive Intelligence SPA

## Problem Statement

Product managers evaluating the competitive landscape for time-tracking tools have no
fast, visual way to understand where competitors are winning or losing on specific
features, or to drill into the Reddit signal behind any data point. Without this,
roadmap prioritization is based on instinct rather than live user sentiment at scale.

## Evidence

- Scraper and analyzer pipelines are live and populating `mention_analyses` with
  sentiment-scored, feature-tagged competitor mentions from Reddit.
- No dashboard exists yet — all data is queryable only via Supabase SQL Editor.
- User is non-technical PM with no time to write queries; raw data is invisible to them today.
- GTM is underway — roadmap decisions are being made now, not later.

## Proposed Solution

A single `index.html` file — no build step, no Node, Supabase JS via CDN — that gives
PMs a three-view read-only dashboard: a sentiment heatmap across competitors × features,
a drill-down pain points view, and an opportunity matrix showing feature gaps across the
market. Keyword Manager is explicitly deferred to v2.

## Key Hypothesis

We believe a fast, scannable sentiment heatmap with one-click drill-down will let PMs
identify the top 3 roadmap opportunities in under 5 minutes. We'll know we're right
when a PM can open the dashboard cold and correctly identify the weakest competitor
feature cluster without assistance.

## What We're NOT Building

- **Keyword Manager tab** — deferred to v2; search terms are managed via Supabase directly for now
- **Authentication / auth wall** — internal use only; anon key with RLS is sufficient
- **Real-time streaming updates** — dashboard queries on load/filter change; no live push
- **Mobile-optimised layout** — target is desktop PM workflow; responsive is nice-to-have not a blocker
- **Data export / CSV download** — out of scope for v1
- **Historical trend charting** — time-series view deferred; v1 is point-in-time snapshot

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| Time to first insight | < 5 min from cold open | Manual usability test with PM |
| Drill-down reachable in | ≤ 2 clicks from heatmap | Click path audit |
| Dashboard load time | < 3s on broadband | Browser DevTools Network tab |
| Zero blank states | All views show data or a clear empty-state message | QA pass |

## Open Questions

- [x] **Row count**: ~350 rows in `mention_analyses` — client-side pagination at 50 rows/page is sufficient; no cursor pagination needed.
- [x] **Heatmap default**: Show all 18 competitors and all 53 features by default; provide a multi-select filter to hide/show competitors or features per view.
- [x] **Color palette**: Pastel / muted / semi-transparent red→yellow→green. Not saturated. Should feel calm and readable, not alarming.
- [x] **Threshold persistence**: Reset to 0.45 on every load (v1 simplicity). localStorage persistence deferred to v2.

---

## Users & Context

**Primary User**
- **Who**: Product Manager at a time-tracking startup, non-technical, rarely opens SQL editors
- **Current behavior**: Asks engineers for ad-hoc data pulls, reads anecdotal Reddit threads manually, synthesizes competitor info from G2/Capterra reviews
- **Trigger**: Weekly roadmap sync or investor prep — needs to justify feature prioritization with market signal
- **Success state**: Opens dashboard, sees the heatmap, spots a cluster of red for a competitor on a feature category, clicks in, reads 2-3 supporting quotes, leaves with a slide-ready insight

**Job to Be Done**
When preparing roadmap prioritization, I want to see where competitors are losing user
trust on specific features, so I can identify high-impact gaps our product can own.

**Non-Users**
- Engineers (they use Supabase directly)
- Executives who want a slide deck (they'll get screenshots from the PM)
- External stakeholders (no auth — dashboard must never be public-facing)

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | Sentiment heatmap: competitors × features, color-coded by avg score | Primary entry point; the whole point of the tool |
| Must | Click-through from heatmap cell → filtered Pain Points view | Without drill-down the heatmap is untrustworthy |
| Must | Supporting quote visible per mention row (inline or tooltip) | PM needs to read the actual Reddit text to trust the score |
| Must | Date range filter on heatmap | Sentiment shifts over time; stale data misleads |
| Must | Subreddit filter on heatmap | Signal quality varies by subreddit |
| Should | Opportunity matrix view: features where all competitors avg below threshold | Directly answers "what gap should we build into?" |
| Should | Pain Points: sort by sentiment score asc and Reddit score | Two different prioritization angles |
| Should | Pain Points: pagination (50 rows per page) | Table can't render 10k rows |
| Should | Opportunity matrix: adjustable threshold slider | Default 0.45 but PMs may want to tighten/loosen |
| Should | Competitor + feature multi-select filter (hide/show per view) | Confirmed requirement; 18 × 53 is dense without filtering |
| Could | Feature category grouping in heatmap (collapse related features) | 53 features is a lot of rows; grouping improves scannability |
| Could | Empty state illustrations | Polish, not function |
| Won't | Keyword Manager | v2 — internal Supabase access is acceptable for now |
| Won't | Auth / login | Internal tool; RLS anon key is sufficient |
| Won't | Mobile layout | PM workflow is desktop |

### MVP Scope

Three working tabs: Heatmap, Pain Points, Opportunity Matrix. Heatmap is the entry point.
Pain Points is reachable by clicking any heatmap cell. All three load real data from
Supabase. Filters work. Supporting quotes are readable inline. No Keyword Manager tab.

### User Flow (Critical Path)

```
Open index.html
  → Heatmap loads (competitors × features, color-coded)
  → PM applies date filter (last 90 days)
  → PM spots red cluster: "Clockify — invoicing"
  → PM clicks cell
  → Pain Points view opens, pre-filtered to Clockify + invoicing
  → PM sees 10 mentions sorted by lowest sentiment
  → PM reads supporting quotes inline
  → PM switches to Opportunity Matrix tab
  → PM sees features where ALL competitors score below 0.45
  → PM leaves with 2 actionable roadmap items
```

---

## Technical Approach

**Feasibility**: HIGH — all data is already in Supabase; queries are straightforward
GROUP BY aggregations and filtered selects. No backend needed.

**Architecture Notes**

- Single `index.html` file — vanilla JS, no framework, no build
- Supabase JS v2 from CDN: `https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2`
- `SUPABASE_URL` and `SUPABASE_KEY` (anon) injected as `const` at top of file
- Anon key is read-only for `raw_mentions` and `mention_analyses` (RLS enforced)
- Heatmap query: `mention_analyses` GROUP BY competitor, feature → AVG(sentiment_score)
- Subreddit filter requires JOIN to `raw_mentions` on `mention_id`
- Pain Points query: filtered SELECT with `.range()` pagination
- Opportunity matrix: computed client-side from heatmap data (all competitors below threshold per feature)
- Tab state managed in URL hash (`#heatmap`, `#pain-points`, `#opportunity`) for shareability

**Key Queries**

```js
// Heatmap (no subreddit filter)
supabase.from('mention_analyses')
  .select('competitor, feature, sentiment_score')
  .gte('analyzed_at', fromDate)
  .lte('analyzed_at', toDate)

// Heatmap with subreddit filter (requires join via mention_id → raw_mentions.subreddit)
// Supabase JS does not support JOIN natively — use a Postgres view or RPC
// Recommended: create a view `mention_analyses_enriched` that joins raw_mentions.subreddit
// Or: fetch mention_ids for the subreddit filter first, then filter analyses

// Pain Points
supabase.from('mention_analyses')
  .select('*, raw_mentions(subreddit, url, score)')
  .eq('competitor', competitor)
  .eq('feature', feature)
  .order('sentiment_score', { ascending: true })
  .range(page * 50, page * 50 + 49)
```

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Subreddit filter requires JOIN — Supabase JS has no native JOIN | HIGH | Create a Postgres view `mention_analyses_enriched` in schema.sql or use `.select('*, raw_mentions!inner(subreddit)')` with embedded select |
| 18 × 53 heatmap = 954 cells — may be slow to render in plain DOM | MEDIUM | Use CSS grid; only render visible rows; consider virtual scrolling or feature-group collapse |
| Haiku sometimes produces partial data — some competitor/feature combos have 0 rows | LOW | Show "no data" state in cell rather than blank |
| CDN unavailability | LOW | Acceptable for internal tool; document fallback URL |

**Security Notes**

- Anon key is visible in the HTML source — acceptable for internal-only use. **Never deploy this dashboard to a public URL.**
- RLS ensures anon key cannot write to `raw_mentions` or `mention_analyses`.
- No user input is passed to Supabase queries unsanitized — all filters use `.eq()` / `.gte()` parameterized methods.
- If dashboard is ever made externally accessible, move to an Edge Function with server-rendered tokens.

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | Scaffold & Data Layer | HTML shell, Supabase client init, tab routing, heatmap data fetch | complete | - | - | `.claude/PRPs/plans/completed/dashboard.plan.md` |
| 2 | Heatmap View | Render competitors × features grid, color scale, date/subreddit filters | complete | - | 1 | `.claude/PRPs/plans/completed/dashboard.plan.md` |
| 3 | Pain Points View | Paginated mention table, inline quotes, sort controls, pre-filter from heatmap click | complete | with 4 | 2 | `.claude/PRPs/plans/completed/dashboard.plan.md` |
| 4 | Opportunity Matrix View | Feature gap table, threshold slider, computed from heatmap data | complete | with 3 | 2 | `.claude/PRPs/plans/completed/dashboard.plan.md` |
| 5 | Polish & QA | Empty states, loading skeletons, error handling, cross-browser check | complete | - | 3, 4 | `.claude/PRPs/reports/dashboard-report.md` |

### Phase Details

**Phase 1: Scaffold & Data Layer**
- Goal: Working HTML file with Supabase connected and tab navigation functional
- Scope: `index.html` skeleton, CDN imports, `const SUPABASE_URL / SUPABASE_KEY`, tab switcher, one test query to verify connection
- Success signal: Opening the file in a browser shows three tab buttons; console shows data from `mention_analyses`

**Phase 2: Heatmap View**
- Goal: Full competitor × feature grid rendered with color-coded sentiment, date and subreddit filters working
- Scope: Aggregate query, CSS grid render, red→yellow→green color scale (0→0.5→1), date range picker, subreddit dropdown, click handler that routes to Pain Points with filter state
- Success signal: Heatmap renders all competitors and features with correct colors; clicking a cell opens Pain Points pre-filtered

**Phase 3: Pain Points View**
- Goal: Paginated, sortable mention list with supporting quotes visible inline
- Scope: Filtered `mention_analyses` query with embedded `raw_mentions` join, 50-row pagination, sort by sentiment score or Reddit score, supporting_quote shown as expandable row or inline text, subreddit and URL link per row
- Success signal: Clicking a heatmap cell shows correct filtered mentions; quotes are readable without leaving the view

**Phase 4: Opportunity Matrix View**
- Goal: Feature gap identification — show features where all competitors score below threshold
- Scope: Compute from heatmap data (no extra query needed), threshold slider (0.1–1.0, default 0.45), table of qualifying features sorted by avg score asc
- Success signal: Adjusting slider updates the table instantly; features shown match manual SQL verification

**Phase 5: Polish & QA**
- Goal: Production-quality feel for PM audience — no blank states, no layout breaks, clear loading feedback
- Scope: Loading spinners/skeletons on all data fetches, empty-state messages ("No data for this combination"), error banners for Supabase failures, basic responsive check at 1280px and 1440px
- Success signal: QA pass — all views handle zero-data and network-error states gracefully

### Parallelism Notes

Phases 3 and 4 can be built in parallel once Phase 2 (heatmap + filter state) is complete,
since both receive their filter context from the heatmap click handler and neither depends
on the other's data.

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| Keyword Manager scope | Deferred to v2 | Include in v1 | PM focus is insight consumption, not data management; keeps scope tight |
| Auth strategy | Anon key, no auth wall | Edge Function with token, Supabase Auth | Internal tool; complexity not justified for v1 |
| Framework | Vanilla JS | React, Vue, Svelte | No build step constraint from CLAUDE.md; CDN-only requirement |
| Subreddit filter join | Postgres view or embedded select | Client-side join | Supabase JS `.select('*, raw_mentions!inner(subreddit)')` handles this without a separate view |
| Opportunity matrix computation | Client-side from heatmap payload | Separate DB query | Heatmap already fetches all competitor × feature averages; reuse avoids a second round-trip |
| Color scale | Pastel/muted/semi-transparent red→yellow→green | Saturated traffic-light colors, blue scale | Confirmed by user; calm aesthetic, readable without feeling alarmist |
| Threshold persistence | Reset to 0.45 on every load | localStorage | v1 simplicity; localStorage deferred to v2 |

---

## Research Summary

**Market Context**
Existing competitive intelligence dashboards (G2, Crayon, Klue) are expensive SaaS tools
aimed at enterprise GTM teams. They do not expose raw Reddit sentiment or feature-level
granularity at this price point. The closest open-source equivalent is hand-rolled
Metabase/Redash dashboards connected to a scraper database — which still requires
technical setup. This tool fills a gap: zero-setup, PM-native, Reddit-native sentiment at
feature resolution.

**Technical Context**
- `mention_analyses` schema confirmed: competitor, feature, sentiment_score, sentiment_label, supporting_quote, analyzed_at, mention_id (FK to raw_mentions)
- `raw_mentions` has subreddit, url, score — all useful for Pain Points view
- 18 competitors × 53 features = 954 possible heatmap cells; many will be empty (no data)
- Supabase JS v2 supports embedded selects: `.select('*, raw_mentions!inner(subreddit, url, score)')` — avoids a separate view for the Pain Points join
- Indexes already exist on `(competitor, feature)`, `analyzed_at`, `mention_id`, `subreddit` — all filter paths are indexed

---

*Generated: 2026-05-31*
*Status: DRAFT - needs validation*
