# Plan: Vibecheck Dashboard — index.html SPA (All Phases)

## Summary

A single `index.html` file — vanilla JS, Supabase JS v2 from CDN, no build step — that
gives PMs a three-tab competitive intelligence dashboard: sentiment heatmap
(competitors × features), pain points drill-down, and opportunity matrix. All data lives
in Supabase; the anon key is used client-side with RLS enforced.

## User Story

As a product manager preparing roadmap prioritization,
I want a visual dashboard that shows where competitors are losing user trust on specific
features,
So that I can identify high-impact gaps to build into our product roadmap.

## Problem → Solution

PM manually queries Supabase or asks engineers for data → PM opens `index.html` in
a browser, sees a color-coded heatmap, clicks a cell, reads supporting Reddit quotes,
identifies opportunities.

## Metadata

- **Complexity**: Large (single file, but substantial logic + layout)
- **Source PRD**: `.claude/PRPs/prds/dashboard.prd.md`
- **PRD Phase**: All 5 phases (planned end-to-end)
- **Estimated Files**: 1 (`index.html`) — all CSS, JS, and HTML in one file

---

## UX Design

### Before

```
┌─────────────────────────────────────┐
│  Supabase SQL Editor                │
│                                     │
│  SELECT competitor, feature,        │
│  AVG(sentiment_score) FROM ...      │
│                                     │
│  [PM doesn't use this]              │
└─────────────────────────────────────┘
```

### After

```
┌────────────────────────────────────────────────────────────────────┐
│  Vibecheck                                                         │
│  ┌──────────┐ ┌─────────────┐ ┌────────────────────┐              │
│  │ Heatmap  │ │ Pain Points │ │ Opportunity Matrix │              │
│  └──────────┘ └─────────────┘ └────────────────────┘              │
│                                                                    │
│  Filters: [Date range ▼]  [Subreddit ▼]  [Competitors ▼]          │
│           [Features ▼]                                             │
│                                                                    │
│  HEATMAP                                                           │
│         │ invoicing │ AI features │ ease of use │ pricing │ ...   │
│  ───────┼───────────┼─────────────┼─────────────┼─────────┤       │
│  Toggl  │   🟩 0.72 │    🟨 0.51  │   🟩 0.68   │ 🟥 0.31 │       │
│  Clockify│  🟥 0.28 │    🟩 0.66  │   🟨 0.50   │ 🟥 0.34 │       │
│  Harvest │  🟨 0.48 │    ——       │   🟩 0.71   │ 🟨 0.42 │       │
│  ...    │           │             │             │         │       │
└────────────────────────────────────────────────────────────────────┘
```

### Interaction Changes

| Touchpoint | Before | After |
|---|---|---|
| Data access | SQL Editor | Browser tab |
| Competitor comparison | Manual, ad-hoc | Heatmap grid, instant |
| Drill-down | New SQL query | Click heatmap cell |
| Quote reading | Raw DB text | Inline row in table |
| Opportunity discovery | Mental model | Opportunity Matrix tab |

---

## Architecture Decision: Layout

**Decision: Single `<body>` with tab navigation via `display: none` toggling.**

Rationale:
- All data is loaded on page-init (heatmap fetch ~350 rows — fast). Pain Points fetches on tab/cell click. Opportunity Matrix is computed from heatmap data client-side.
- No routing library needed. URL hash (`#heatmap`, `#pain-points`, `#opportunity`) drives visible section.
- Three `<section id="...">` elements; JS shows/hides via `classList`. No page reload.
- Simpler than a SPA router; correct for a 1-file tool.

**NOT chosen: separate HTML files per view** — breaks filter state sharing and requires file-serving infrastructure. Opening `file://` in a browser is the target.

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `schema.sql` | 1–125 | Exact column names, types, RLS policies, indexes |
| P0 | `Documentation-Supabase.md` | 141–274 | JS client patterns: select, filters, join, error handling |
| P0 | `CLAUDE.md` | (dashboard section) | Competitors list, features list, subreddits, JS usage patterns |
| P1 | `index.html` | all | Current placeholder — replace entirely |

---

## Patterns to Mirror

### SUPABASE_INIT
```js
// SOURCE: Documentation-Supabase.md:159-168
const { createClient } = supabase
const SUPABASE_URL = 'https://your-project-ref.supabase.co'
const SUPABASE_ANON_KEY = 'your-anon-key'
const client = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)
```

### SUPABASE_SELECT_WITH_JOIN
```js
// SOURCE: Documentation-Supabase.md:218-222
const { data, error } = await client
  .from('mention_analyses')
  .select('*, raw_mentions(subreddit, score, url)')
  .eq('competitor', 'Toggl')
```

### SUPABASE_ERROR_HANDLING
```js
// SOURCE: Documentation-Supabase.md:254-264
const { data, error } = await client.from('mention_analyses').select('*')
if (error) {
  console.error('Supabase error:', error.message)
  return
}
// use data
```

### SUPABASE_PAGINATION
```js
// SOURCE: Documentation-Supabase.md:208-215
const { data } = await client
  .from('mention_analyses')
  .select('*')
  .order('sentiment_score', { ascending: true })
  .range(page * 50, page * 50 + 49)
```

### SUPABASE_DATE_FILTER
```js
// SOURCE: Documentation-Supabase.md:196-200 (gte/lte pattern)
const { data } = await client
  .from('mention_analyses')
  .select('competitor, feature, sentiment_score')
  .gte('analyzed_at', fromDate)   // fromDate = ISO string e.g. '2026-01-01'
  .lte('analyzed_at', toDate)
```

### SUPABASE_SUBREDDIT_FILTER
```js
// Subreddit lives on raw_mentions, not mention_analyses.
// Use embedded select with !inner to filter only rows where the join matches.
// SOURCE: Documentation-Supabase.md:218-222 + schema.sql FK
const { data, error } = await client
  .from('mention_analyses')
  .select('competitor, feature, sentiment_score, raw_mentions!inner(subreddit)')
  .eq('raw_mentions.subreddit', subredditValue)
  .gte('analyzed_at', fromDate)
  .lte('analyzed_at', toDate)
// NOTE: !inner ensures only rows with a matching raw_mention are returned.
// If subredditValue is 'all', omit the .eq('raw_mentions.subreddit', ...) filter.
```

### COLOR_SCALE
```js
// Pastel, muted, semi-transparent. Map score 0→1 to hue 0→120 (red→green).
// Low saturation (40%), high lightness (88%), slight transparency.
function scoreToColor(score) {
  if (score === null) return 'hsl(0,0%,94%)'  // no-data grey
  const hue = Math.round(score * 120)           // 0=red, 60=yellow, 120=green
  return `hsla(${hue}, 40%, 88%, 0.85)`
}
// Dark text on all cells for readability (lightness 88% is always readable with dark text).
```

### TAB_ROUTING
```js
// SOURCE: architecture decision above
function showTab(name) {   // name: 'heatmap' | 'pain-points' | 'opportunity'
  document.querySelectorAll('.tab-section').forEach(s => s.hidden = true)
  document.getElementById(`section-${name}`).hidden = false
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'))
  document.querySelector(`.tab-btn[data-tab="${name}"]`).classList.add('active')
  location.hash = name
}
window.addEventListener('hashchange', () => {
  const tab = location.hash.replace('#', '') || 'heatmap'
  showTab(tab)
})
```

### HEATMAP_DATA_STRUCTURE
```js
// After fetching all mention_analyses rows, build a lookup map for the grid.
// Key: `${competitor}|||${feature}`, value: { scores: [], avg: number }
function buildHeatmapMap(rows) {
  const map = {}
  for (const row of rows) {
    const key = `${row.competitor}|||${row.feature}`
    if (!map[key]) map[key] = { scores: [] }
    map[key].scores.push(Number(row.sentiment_score))
  }
  for (const key of Object.keys(map)) {
    const s = map[key].scores
    map[key].avg = s.reduce((a, b) => a + b, 0) / s.length
  }
  return map
}
```

### HEATMAP_RENDER
```js
// HTML table (not CSS grid) — tables give row/column semantics for free,
// scroll independently on overflow-x, and align headers naturally.
// Each cell is a <td> with data-competitor and data-feature attributes for click routing.
function renderHeatmap(competitors, features, map) {
  const table = document.getElementById('heatmap-table')
  // Header row
  let html = '<thead><tr><th></th>'
  for (const f of features) html += `<th title="${f}">${f}</th>`
  html += '</tr></thead><tbody>'
  // Body rows
  for (const c of competitors) {
    html += `<tr><th>${c}</th>`
    for (const f of features) {
      const key = `${c}|||${f}`
      const cell = map[key]
      const score = cell ? cell.avg : null
      const bg = scoreToColor(score)
      const label = score !== null ? score.toFixed(2) : '—'
      html += `<td style="background:${bg}" data-competitor="${c}" data-feature="${f}"
                   title="${c} · ${f} · ${label}">${label}</td>`
    }
    html += '</tr>'
  }
  html += '</tbody>'
  table.innerHTML = html
  // Click delegation — one listener on the table
  table.addEventListener('click', e => {
    const td = e.target.closest('td[data-competitor]')
    if (!td) return
    openPainPoints(td.dataset.competitor, td.dataset.feature)
  })
}
```

### FILTER_STATE
```js
// Single source of truth for all active filters. Passed to every query/render.
// Mutated by filter UI event handlers; triggers re-fetch + re-render.
const filters = {
  dateFrom: null,       // ISO string or null (null = no lower bound)
  dateTo: null,         // ISO string or null
  subreddit: 'all',     // subreddit name or 'all'
  competitors: [],      // [] = show all; populated from COMPETITORS constant
  features: [],         // [] = show all; populated from FEATURES constant
}

// Re-fetch heatmap data and re-render when any filter changes.
async function applyFilters() {
  showLoading('heatmap')
  const rows = await fetchHeatmapRows(filters)
  const activeCompetitors = filters.competitors.length ? filters.competitors : COMPETITORS
  const activeFeatures = filters.features.length ? filters.features : FEATURES
  const map = buildHeatmapMap(rows)
  renderHeatmap(activeCompetitors, activeFeatures, map)
  // Opportunity matrix reads from same map — recompute too
  renderOpportunityMatrix(map, activeCompetitors, activeFeatures, opportunityThreshold)
  hideLoading('heatmap')
}
```

### PAIN_POINTS_QUERY
```js
// Fetch paginated mention rows for a given competitor + feature.
// Embeds raw_mentions join for subreddit + url + reddit score.
async function fetchPainPoints(competitor, feature, sortBy, page) {
  const ascending = sortBy === 'sentiment'  // sentiment asc = worst first
  let q = client
    .from('mention_analyses')
    .select('id, competitor, feature, sentiment_score, sentiment_label, supporting_quote, analyzed_at, mention_id, raw_mentions(subreddit, url, score)')
    .eq('competitor', competitor)
    .eq('feature', feature)
    .order(sortBy === 'sentiment' ? 'sentiment_score' : 'raw_mentions.score', { ascending })
    .range(page * 50, page * 50 + 49)
  const { data, error } = await q
  if (error) { showError(error.message); return [] }
  return data
}
// NOTE: ordering by raw_mentions.score (a joined column) may not be supported
// in Supabase JS v2 — if it fails, fetch all rows and sort client-side instead.
// With ~350 total rows, client-side sort is safe.
```

### OPPORTUNITY_MATRIX_COMPUTE
```js
// From the heatmap map, find features where every competitor's avg score is below threshold.
// If a competitor has no data for a feature, skip that competitor (not a disqualifier).
function computeOpportunities(map, competitors, features, threshold) {
  return features
    .map(feature => {
      const scores = competitors
        .map(c => map[`${c}|||${feature}`]?.avg)
        .filter(s => s !== undefined)   // skip competitors with no data
      if (scores.length === 0) return null
      const allBelow = scores.every(s => s < threshold)
      const avgScore = scores.reduce((a, b) => a + b, 0) / scores.length
      return allBelow ? { feature, avgScore, competitorCount: scores.length } : null
    })
    .filter(Boolean)
    .sort((a, b) => a.avgScore - b.avgScore)
}
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `index.html` | REWRITE | Current file is a 10-line placeholder |

## NOT Building

- Keyword Manager tab (v2)
- Authentication / login wall
- Mobile-optimised layout
- Data export / CSV
- Historical trend charting / time-series
- Real-time updates / websocket push
- localStorage persistence for threshold (v2)

---

## Step-by-Step Tasks

### Task 1: Document Structure & Constants

- **ACTION**: Write the full HTML document skeleton with all structural sections
- **IMPLEMENT**:
  ```html
  <!DOCTYPE html>
  <html lang="en">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Vibecheck — Competitive Intelligence</title>
    <style>/* all CSS inline — Task 2 */</style>
  </head>
  <body>
    <!-- constants injected here — Task 1 -->
    <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
    <script>
      const SUPABASE_URL = 'REPLACE_ME'
      const SUPABASE_ANON_KEY = 'REPLACE_ME'
      const { createClient } = supabase
      const client = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)

      const COMPETITORS = ['Rize','Timely','Reclaim.ai','TimeCamp','Memtime','Timeular',
        'Clockk','Hubstaff','Toggl Track','Toggl','Clockify','Harvest','RescueTime',
        'Carly AI','Replicon','ZeroTime','Kickidler','Flowace']

      const FEATURES = ['automatic time tracking','manual time tracking','voice time tracking',
        'voice capture','speech to text','idle detection','offline tracking','timer accuracy',
        'background tracking','browser extension','desktop app','mobile app','GPS tracking',
        'invoicing','billing','payment processing','expense tracking','budget tracking',
        'AI features','smart scheduling','AI insights','AI reports','meeting detection',
        'calendar AI','employee monitoring','screenshots','team analytics','team dashboard',
        'project budgeting','payroll integration','geofencing','calendar sync',
        'Jira integration','Asana integration','Slack integration','QuickBooks integration',
        'Xero integration','API access','data export','excel export','Zapier integration',
        'project tool integrations','accounting integrations','ease of use','onboarding',
        'customer support','UI design','pricing','free tier','privacy concerns',
        'surveillance concerns','accuracy','reliability']

      const SUBREDDITS = ['all','freelance','freelancedesign','consulting','smallbusiness',
        'webdev','graphic_design','productivityapps','workforcemanagement','Entrepreneur',
        'productivity','timetracking','timetrackingsoftware','remotework']
    </script>

    <header>...</header>
    <nav class="tab-bar">...</nav>

    <section id="section-heatmap" class="tab-section">...</section>
    <section id="section-pain-points" class="tab-section" hidden>...</section>
    <section id="section-opportunity" class="tab-section" hidden>...</section>

    <script>/* all JS — Tasks 3–8 */</script>
  </body>
  </html>
  ```
- **MIRROR**: SUPABASE_INIT, TAB_ROUTING
- **GOTCHA**: The CDN script must appear BEFORE the `createClient` call. Put `<script src="...supabase-js@2">` in `<head>` or just before the constants block.
- **VALIDATE**: Open file in browser → no console errors → `client` object exists in DevTools console

---

### Task 2: CSS Design System

- **ACTION**: Write all CSS inline in `<style>`. No external stylesheets.
- **IMPLEMENT**: Design tokens + layout rules:

  ```css
  :root {
    /* Palette — muted, editorial */
    --bg: #f8f7f5;
    --surface: #ffffff;
    --border: #e8e5e0;
    --text-primary: #1a1916;
    --text-secondary: #6b6760;
    --text-muted: #a09d99;
    --accent: #4a6fa5;       /* tab active, links */
    --accent-light: #e8eef7;

    /* Sentiment colors — pastel, muted, semi-transparent */
    /* Applied via inline style from scoreToColor() — not CSS vars */

    /* Typography */
    --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --font-mono: 'SF Mono', 'Fira Code', monospace;

    /* Spacing */
    --space-xs: 4px;
    --space-sm: 8px;
    --space-md: 16px;
    --space-lg: 24px;
    --space-xl: 40px;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: var(--font); background: var(--bg); color: var(--text-primary); }

  /* Header */
  header { padding: var(--space-md) var(--space-xl); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: var(--space-md); background: var(--surface); }
  header h1 { font-size: 1.1rem; font-weight: 600; letter-spacing: -0.02em; }

  /* Tab bar */
  .tab-bar { display: flex; gap: 0; border-bottom: 1px solid var(--border);
    background: var(--surface); padding: 0 var(--space-xl); }
  .tab-btn { padding: var(--space-sm) var(--space-md); font-size: 0.875rem;
    border: none; background: none; cursor: pointer; color: var(--text-secondary);
    border-bottom: 2px solid transparent; margin-bottom: -1px; transition: color 150ms; }
  .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 500; }
  .tab-btn:hover:not(.active) { color: var(--text-primary); }

  /* Section layout */
  .tab-section { padding: var(--space-lg) var(--space-xl); }

  /* Filter bar */
  .filter-bar { display: flex; gap: var(--space-sm); flex-wrap: wrap;
    margin-bottom: var(--space-lg); align-items: center; }
  .filter-bar label { font-size: 0.8rem; color: var(--text-secondary); }
  .filter-bar select, .filter-bar input[type="date"] {
    font-size: 0.875rem; padding: 5px 8px; border: 1px solid var(--border);
    border-radius: 6px; background: var(--surface); color: var(--text-primary); }

  /* Heatmap table */
  .heatmap-wrap { overflow-x: auto; overflow-y: auto; max-height: 70vh;
    border: 1px solid var(--border); border-radius: 8px; }
  #heatmap-table { border-collapse: collapse; font-size: 0.78rem; }
  #heatmap-table th { background: var(--surface); position: sticky;
    top: 0; z-index: 2; padding: var(--space-xs) var(--space-sm);
    text-align: left; font-weight: 500; color: var(--text-secondary);
    border-bottom: 1px solid var(--border); white-space: nowrap; }
  #heatmap-table tbody th { position: sticky; left: 0; background: var(--surface);
    z-index: 1; min-width: 110px; font-weight: 500; border-right: 1px solid var(--border); }
  #heatmap-table td { padding: 6px 10px; text-align: center; cursor: pointer;
    border: 1px solid rgba(0,0,0,0.04); font-variant-numeric: tabular-nums;
    transition: opacity 120ms; min-width: 64px; }
  #heatmap-table td:hover { opacity: 0.75; outline: 2px solid var(--accent); }

  /* Sentiment badge (Pain Points) */
  .badge { display: inline-block; padding: 2px 7px; border-radius: 99px;
    font-size: 0.75rem; font-weight: 500; }

  /* Pain points table */
  .pp-table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
  .pp-table th { text-align: left; padding: var(--space-sm) var(--space-md);
    border-bottom: 2px solid var(--border); color: var(--text-secondary);
    font-weight: 500; font-size: 0.8rem; cursor: pointer; user-select: none; }
  .pp-table th:hover { color: var(--text-primary); }
  .pp-table td { padding: var(--space-sm) var(--space-md);
    border-bottom: 1px solid var(--border); vertical-align: top; }
  .pp-table tr:last-child td { border-bottom: none; }
  .pp-quote { color: var(--text-secondary); font-size: 0.825rem;
    font-style: italic; margin-top: 3px; }

  /* Opportunity table */
  .opp-table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
  .opp-table th { text-align: left; padding: var(--space-sm) var(--space-md);
    border-bottom: 2px solid var(--border); color: var(--text-secondary); font-weight: 500; }
  .opp-table td { padding: var(--space-sm) var(--space-md);
    border-bottom: 1px solid var(--border); }

  /* Threshold slider */
  .slider-wrap { display: flex; gap: var(--space-sm); align-items: center;
    margin-bottom: var(--space-md); }
  .slider-wrap input[type="range"] { width: 200px; accent-color: var(--accent); }
  .slider-wrap span { font-size: 0.875rem; color: var(--text-secondary); min-width: 3ch; }

  /* Pagination */
  .pagination { display: flex; gap: var(--space-sm); align-items: center;
    margin-top: var(--space-md); font-size: 0.875rem; color: var(--text-secondary); }
  .pagination button { padding: 4px 10px; border: 1px solid var(--border);
    border-radius: 4px; background: var(--surface); cursor: pointer; font-size: 0.875rem; }
  .pagination button:disabled { opacity: 0.4; cursor: default; }

  /* Loading / empty states */
  .loading { padding: var(--space-xl); text-align: center; color: var(--text-muted);
    font-size: 0.875rem; }
  .empty-state { padding: var(--space-xl); text-align: center; color: var(--text-muted); }
  .empty-state p { margin-bottom: var(--space-sm); }
  .error-banner { padding: var(--space-sm) var(--space-md); background: #fef2f2;
    border: 1px solid #fecaca; border-radius: 6px; color: #b91c1c;
    font-size: 0.875rem; margin-bottom: var(--space-md); }

  /* Back link */
  .back-link { font-size: 0.8rem; color: var(--accent); cursor: pointer;
    margin-bottom: var(--space-md); display: inline-block; }
  .back-link:hover { text-decoration: underline; }

  /* Section heading */
  .section-title { font-size: 1rem; font-weight: 600; margin-bottom: var(--space-xs); }
  .section-subtitle { font-size: 0.875rem; color: var(--text-secondary);
    margin-bottom: var(--space-lg); }

  /* Multi-select pills */
  .pill-select { display: flex; gap: 6px; flex-wrap: wrap; }
  .pill { padding: 3px 10px; border-radius: 99px; font-size: 0.78rem;
    border: 1px solid var(--border); background: var(--surface); cursor: pointer;
    color: var(--text-secondary); transition: all 100ms; }
  .pill.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  ```

- **GOTCHA**: `position: sticky` on the first `<th>` column AND header row simultaneously requires `z-index: 1` on the row header and `z-index: 2` on the top-left corner cell. Without this, the corner overlaps incorrectly on scroll.
- **VALIDATE**: Open in browser → tab switching works → heatmap area scrolls independently

---

### Task 3: Filter UI & State

- **ACTION**: Render filter controls and wire them to the `filters` state object
- **IMPLEMENT**:

  **Heatmap filter bar HTML:**
  ```html
  <div class="filter-bar">
    <label>From <input type="date" id="filter-from"></label>
    <label>To <input type="date" id="filter-to"></label>
    <label>Subreddit
      <select id="filter-subreddit">
        <!-- populated by JS from SUBREDDITS -->
      </select>
    </label>
    <button id="btn-apply-filters">Apply</button>
    <button id="btn-reset-filters">Reset</button>
  </div>

  <!-- Multi-select competitor picker -->
  <details>
    <summary style="cursor:pointer;font-size:0.8rem;color:var(--text-secondary);margin-bottom:8px">
      Filter competitors (showing all)
    </summary>
    <div id="competitor-pills" class="pill-select"></div>
  </details>

  <!-- Multi-select feature picker -->
  <details>
    <summary style="cursor:pointer;font-size:0.8rem;color:var(--text-secondary);margin-bottom:8px;margin-top:8px">
      Filter features (showing all)
    </summary>
    <div id="feature-pills" class="pill-select"></div>
  </details>
  ```

  **JS wiring:**
  ```js
  function initFilters() {
    // Populate subreddit dropdown
    const sub = document.getElementById('filter-subreddit')
    SUBREDDITS.forEach(s => {
      const opt = document.createElement('option')
      opt.value = s; opt.textContent = s === 'all' ? 'All subreddits' : r/`${s}`
      sub.appendChild(opt)
    })

    // Competitor pills
    const cpills = document.getElementById('competitor-pills')
    COMPETITORS.forEach(c => {
      const pill = document.createElement('button')
      pill.className = 'pill active'; pill.textContent = c
      pill.dataset.value = c
      pill.addEventListener('click', () => {
        pill.classList.toggle('active')
        filters.competitors = [...cpills.querySelectorAll('.pill.active')].map(p => p.dataset.value)
      })
      cpills.appendChild(pill)
    })

    // Feature pills — same pattern as competitor pills
    // ...

    document.getElementById('btn-apply-filters').addEventListener('click', applyFilters)
    document.getElementById('btn-reset-filters').addEventListener('click', () => {
      filters.dateFrom = null; filters.dateTo = null; filters.subreddit = 'all'
      filters.competitors = []; filters.features = []
      document.getElementById('filter-from').value = ''
      document.getElementById('filter-to').value = ''
      sub.value = 'all'
      cpills.querySelectorAll('.pill').forEach(p => p.classList.add('active'))
      // reset feature pills too
      applyFilters()
    })
  }
  ```

- **MIRROR**: FILTER_STATE
- **GOTCHA**: Toggling all pills off should be treated as "show all" (same as all active). Enforce: if `filters.competitors` is empty array, `COMPETITORS` is used.
- **VALIDATE**: Toggle competitors → apply → heatmap re-renders with correct subset

---

### Task 4: Heatmap Data Fetch & Render

- **ACTION**: Fetch all relevant rows from Supabase and render the heatmap table
- **IMPLEMENT**:

  **Fetch function:**
  ```js
  async function fetchHeatmapRows(filters) {
    let q = client
      .from('mention_analyses')
      .select('competitor, feature, sentiment_score, raw_mentions!inner(subreddit)')

    if (filters.dateFrom) q = q.gte('analyzed_at', filters.dateFrom)
    if (filters.dateTo)   q = q.lte('analyzed_at', filters.dateTo)
    if (filters.subreddit !== 'all') q = q.eq('raw_mentions.subreddit', filters.subreddit)

    const { data, error } = await q
    if (error) { showError('heatmap-error', error.message); return [] }
    return data
  }
  ```

  **Render pipeline call (wired to `applyFilters`):**
  ```js
  async function applyFilters() {
    document.getElementById('heatmap-loading').hidden = false
    document.getElementById('heatmap-table').innerHTML = ''
    const rows = await fetchHeatmapRows(filters)
    document.getElementById('heatmap-loading').hidden = true

    const activeCompetitors = filters.competitors.length ? filters.competitors : COMPETITORS
    const activeFeatures    = filters.features.length    ? filters.features    : FEATURES
    const map = buildHeatmapMap(rows)
    renderHeatmap(activeCompetitors, activeFeatures, map)
    renderOpportunityMatrix(map, activeCompetitors, activeFeatures, 0.45)
    heatmapData = { map, activeCompetitors, activeFeatures }  // stored globally for opportunity tab
  }
  ```

- **MIRROR**: SUPABASE_DATE_FILTER, SUPABASE_SUBREDDIT_FILTER, HEATMAP_DATA_STRUCTURE, HEATMAP_RENDER, COLOR_SCALE
- **GOTCHA — subreddit !inner join**: When `raw_mentions!inner(subreddit)` is used but NO subreddit filter is applied, this still works — `!inner` just means rows without a matching raw_mention are excluded (all should have one). If you see 0 rows unexpectedly, switch to `raw_mentions(subreddit)` (left join) and handle null subreddit client-side.
- **GOTCHA — sticky corners**: The top-left cell (row headers × column headers intersection) needs both `position: sticky; top: 0; left: 0; z-index: 3` to pin correctly.
- **VALIDATE**: Heatmap renders with color-coded cells. Check 2-3 cells against manual Supabase query.

---

### Task 5: Pain Points View

- **ACTION**: Build the Pain Points section — sortable, paginated mention table with inline quotes
- **IMPLEMENT**:

  **HTML skeleton:**
  ```html
  <section id="section-pain-points" class="tab-section" hidden>
    <span class="back-link" id="pp-back">← Back to Heatmap</span>
    <div id="pp-header">
      <p class="section-title" id="pp-title">Pain Points</p>
      <p class="section-subtitle" id="pp-subtitle">Click a heatmap cell to filter</p>
    </div>
    <div class="filter-bar">
      Sort by:
      <select id="pp-sort">
        <option value="sentiment">Lowest sentiment first</option>
        <option value="score">Highest Reddit score first</option>
      </select>
    </div>
    <div id="pp-error" class="error-banner" hidden></div>
    <div id="pp-loading" class="loading" hidden>Loading mentions…</div>
    <div id="pp-empty" class="empty-state" hidden><p>No mentions found for this filter.</p></div>
    <table class="pp-table" id="pp-table"></table>
    <div class="pagination" id="pp-pagination"></div>
  </section>
  ```

  **JS — open from heatmap click:**
  ```js
  let ppState = { competitor: null, feature: null, page: 0, sortBy: 'sentiment', total: 0 }

  function openPainPoints(competitor, feature) {
    ppState = { competitor, feature, page: 0, sortBy: 'sentiment', total: 0 }
    document.getElementById('pp-title').textContent = `${competitor} — ${feature}`
    document.getElementById('pp-subtitle').textContent = 'Reddit mentions, sorted by sentiment'
    showTab('pain-points')
    loadPainPoints()
  }

  async function loadPainPoints() {
    const { competitor, feature, page, sortBy } = ppState
    document.getElementById('pp-loading').hidden = false
    document.getElementById('pp-table').innerHTML = ''

    const rows = await fetchPainPoints(competitor, feature, sortBy, page)
    document.getElementById('pp-loading').hidden = true

    if (!rows.length) {
      document.getElementById('pp-empty').hidden = false
      return
    }
    document.getElementById('pp-empty').hidden = true
    renderPainPointsTable(rows)
    renderPagination()
  }

  function renderPainPointsTable(rows) {
    const table = document.getElementById('pp-table')
    let html = `<thead><tr>
      <th>Sentiment</th><th>Label</th><th>Subreddit</th>
      <th>Reddit score</th><th>Quote / Link</th>
    </tr></thead><tbody>`
    for (const row of rows) {
      const score = Number(row.sentiment_score)
      const bg = scoreToColor(score)
      const sub = row.raw_mentions?.subreddit ?? '—'
      const url = row.raw_mentions?.url ?? '#'
      const rscore = row.raw_mentions?.score ?? '—'
      html += `<tr>
        <td><span class="badge" style="background:${bg}">${score.toFixed(2)}</span></td>
        <td style="color:var(--text-secondary);font-size:0.8rem">${row.sentiment_label}</td>
        <td>r/${sub}</td>
        <td style="text-align:right">${rscore}</td>
        <td>
          <div>${row.supporting_quote ?? '—'}</div>
          <a href="${url}" target="_blank" rel="noopener"
             style="font-size:0.75rem;color:var(--accent)">View post →</a>
        </td>
      </tr>`
    }
    html += '</tbody>'
    table.innerHTML = html
  }
  ```

  **Sort wiring:**
  ```js
  document.getElementById('pp-sort').addEventListener('change', e => {
    ppState.sortBy = e.target.value
    ppState.page = 0
    loadPainPoints()
  })
  document.getElementById('pp-back').addEventListener('click', () => showTab('heatmap'))
  ```

  **Pagination:**
  ```js
  function renderPagination() {
    const div = document.getElementById('pp-pagination')
    const { page } = ppState
    div.innerHTML = `
      <button id="pp-prev" ${page === 0 ? 'disabled' : ''}>← Prev</button>
      <span>Page ${page + 1}</span>
      <button id="pp-next">Next →</button>
    `
    document.getElementById('pp-prev').addEventListener('click', () => {
      ppState.page--; loadPainPoints()
    })
    document.getElementById('pp-next').addEventListener('click', () => {
      ppState.page++; loadPainPoints()
    })
    // Hide Next if fewer than 50 rows returned (proxy for last page)
    // Actual row count known after render; disable Next if table rows < 50
    const rows = document.querySelectorAll('#pp-table tbody tr').length
    document.getElementById('pp-next').disabled = rows < 50
  }
  ```

- **MIRROR**: PAIN_POINTS_QUERY, SUPABASE_ERROR_HANDLING, SUPABASE_PAGINATION
- **GOTCHA — sort by raw_mentions.score**: Supabase JS v2 may not support `.order('raw_mentions.score', ...)` on a joined column. If this throws, fetch all rows for that competitor+feature (max ~350 total, safe) and sort client-side. With 350 rows, this is fast enough.
- **GOTCHA — Next button**: Without a `count` query, we don't know total pages. Disable Next when returned rows < 50 (means we're on the last page).
- **VALIDATE**: Click a heatmap cell → Pain Points opens pre-filtered → rows are correct → quotes visible → sort toggle works → Next/Prev navigate

---

### Task 6: Opportunity Matrix View

- **ACTION**: Build the Opportunity Matrix tab — features where all competitors score below threshold
- **IMPLEMENT**:

  ```html
  <section id="section-opportunity" class="tab-section" hidden>
    <p class="section-title">Opportunity Matrix</p>
    <p class="section-subtitle">
      Features where every competitor scores below the threshold — potential gaps to build into.
    </p>
    <div class="slider-wrap">
      <label for="opp-threshold" style="font-size:0.875rem;color:var(--text-secondary)">
        Threshold:
      </label>
      <input type="range" id="opp-threshold" min="0.1" max="1.0" step="0.05" value="0.45">
      <span id="opp-threshold-val">0.45</span>
    </div>
    <div id="opp-empty" class="empty-state" hidden>
      <p>No features below this threshold across all competitors.</p>
      <p>Try raising the threshold.</p>
    </div>
    <table class="opp-table" id="opp-table"></table>
  </section>
  ```

  ```js
  let opportunityThreshold = 0.45

  document.getElementById('opp-threshold').addEventListener('input', e => {
    opportunityThreshold = Number(e.target.value)
    document.getElementById('opp-threshold-val').textContent = opportunityThreshold.toFixed(2)
    if (heatmapData) {
      const { map, activeCompetitors, activeFeatures } = heatmapData
      renderOpportunityMatrix(map, activeCompetitors, activeFeatures, opportunityThreshold)
    }
  })

  function renderOpportunityMatrix(map, competitors, features, threshold) {
    const opportunities = computeOpportunities(map, competitors, features, threshold)
    const empty = document.getElementById('opp-empty')
    const table = document.getElementById('opp-table')

    if (!opportunities.length) {
      empty.hidden = false; table.innerHTML = ''; return
    }
    empty.hidden = true
    let html = `<thead><tr>
      <th>Feature</th>
      <th>Avg sentiment (all competitors)</th>
      <th>Competitor data points</th>
    </tr></thead><tbody>`
    for (const opp of opportunities) {
      const bg = scoreToColor(opp.avgScore)
      html += `<tr>
        <td>${opp.feature}</td>
        <td><span class="badge" style="background:${bg}">${opp.avgScore.toFixed(2)}</span></td>
        <td style="color:var(--text-secondary)">${opp.competitorCount}</td>
      </tr>`
    }
    html += '</tbody>'
    table.innerHTML = html
  }
  ```

- **MIRROR**: OPPORTUNITY_MATRIX_COMPUTE
- **GOTCHA**: Opportunity matrix reads from `heatmapData` (global), which is set only after `applyFilters()` runs. On initial load, ensure `applyFilters()` is called before the user can switch to this tab. Show a "Load heatmap first" message if `heatmapData` is null.
- **VALIDATE**: Move slider → table updates instantly. Verify at least one feature appears at default 0.45 threshold by checking Supabase manually.

---

### Task 7: Empty States, Error Handling, Loading

- **ACTION**: Add consistent loading indicators, error banners, and empty states to all views
- **IMPLEMENT**:

  ```js
  function showError(containerId, msg) {
    const el = document.getElementById(containerId)
    if (!el) return
    el.textContent = `Error loading data: ${msg}`
    el.hidden = false
  }
  function hideError(containerId) {
    const el = document.getElementById(containerId)
    if (el) el.hidden = true
  }
  ```

  Empty state copy:
  - Heatmap, no data: "No mentions found for the selected filters. Try expanding the date range or selecting 'All subreddits'."
  - Pain Points, no data: "No mentions found for [Competitor] — [Feature]. This combination may not appear in the scraped data yet."
  - Opportunity Matrix, no gaps: "No features below [threshold] across all selected competitors. Try raising the threshold."

- **VALIDATE**: Set date range to 1900–1900 → heatmap shows empty state. Network error → error banner appears.

---

### Task 8: Initialization & Tab Wiring

- **ACTION**: Wire everything together in `init()` — called on `DOMContentLoaded`
- **IMPLEMENT**:

  ```js
  let heatmapData = null  // { map, activeCompetitors, activeFeatures }

  async function init() {
    initFilters()
    initTabs()
    await applyFilters()   // load heatmap data on startup
  }

  function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => showTab(btn.dataset.tab))
    })
    // Restore from URL hash
    const hash = location.hash.replace('#', '') || 'heatmap'
    showTab(['heatmap','pain-points','opportunity'].includes(hash) ? hash : 'heatmap')
  }

  document.addEventListener('DOMContentLoaded', init)
  ```

- **VALIDATE**: Page loads → heatmap populates → clicking tabs switches views → URL hash updates → refreshing page restores correct tab

---

## Testing Strategy

No automated test framework (vanilla JS, single file). Validation is manual + Supabase SQL cross-check.

### Manual Test Checklist

| Scenario | Steps | Expected |
|---|---|---|
| Initial load | Open file in browser | Heatmap renders, no console errors |
| Color accuracy | Find a cell with score 0.2 | Should be rose/pink tint |
| Color accuracy | Find a cell with score 0.8 | Should be sage green tint |
| No-data cell | Find competitor+feature with no mentions | Cell shows "—", grey background |
| Date filter | Set range to last 7 days | Heatmap re-renders (fewer cells likely) |
| Subreddit filter | Select 'freelance' | Heatmap re-renders with subset |
| Competitor filter | Deselect all but 'Toggl' | Heatmap shows 1 row |
| Feature filter | Deselect all but 'pricing' | Heatmap shows 1 column |
| Heatmap click | Click a populated cell | Pain Points opens, pre-filtered |
| Pain Points sort | Toggle sort to 'Reddit score' | Rows reorder |
| Pain Points pagination | Click Next | Page 2 loads (or Next disabled on last page) |
| Quote visible | Read a Pain Points row | supporting_quote text visible without expanding |
| Supabase link | Click "View post →" | Opens Reddit URL in new tab |
| Opportunity tab | Switch to Opportunity Matrix | Table shows features below 0.45 |
| Threshold slider | Move to 0.3 | Table updates instantly |
| Empty state | Set threshold to 0.01 | "No features below this threshold" message |
| URL hash | Navigate to #pain-points directly | Pain Points tab active (with no pre-filter) |
| Back link | Click ← Back to Heatmap | Returns to Heatmap tab |
| Error state | Disconnect network, reload | Error banner visible on heatmap |

### Edge Cases Checklist

- [ ] All competitor pills toggled off → treated as "all competitors" (no filter)
- [ ] All feature pills toggled off → treated as "all features" (no filter)
- [ ] Heatmap cell with exactly 1 data point → avg = that point (no division issue)
- [ ] Opportunity Matrix accessed before heatmap loads → graceful message
- [ ] `raw_mentions.url` is null → "View post →" link uses `#` fallback
- [ ] `sentiment_score` is null in DB → cell shows "—" grey
- [ ] Pain Points opened from tab directly (not cell click) → shows "Click a cell to filter" state

---

## Validation Commands

### Browser Validation

```bash
# Open directly in browser (no server needed for read-only Supabase queries)
open index.html

# Or serve locally to avoid any CORS edge cases with file://
python3 -m http.server 8080
# then open http://localhost:8080
```

EXPECT: Dashboard loads, heatmap renders within 3 seconds, no console errors.

### Manual SQL Cross-Check (Supabase SQL Editor)

```sql
-- Verify heatmap cell value for Toggl × pricing
SELECT AVG(sentiment_score)
FROM mention_analyses
WHERE competitor = 'Toggl' AND feature = 'pricing';

-- Verify opportunity matrix at threshold 0.45
SELECT feature, AVG(sentiment_score) as avg_score
FROM mention_analyses
GROUP BY feature
HAVING COUNT(DISTINCT competitor) > 0
  AND MAX(sentiment_score) < 0.45;

-- Verify Pain Points row count for a cell
SELECT COUNT(*) FROM mention_analyses
WHERE competitor = 'Clockify' AND feature = 'invoicing';
```

### Manual Validation Checklist

- [ ] Open `index.html` in Chrome → no errors in DevTools Console
- [ ] Heatmap grid visible with colored cells
- [ ] At least one cell is pink/red (sentiment < 0.4)
- [ ] At least one cell is green (sentiment > 0.65)
- [ ] Clicking a colored cell opens Pain Points filtered correctly
- [ ] Supporting quotes are readable inline
- [ ] Opportunity Matrix shows at least one feature at default threshold
- [ ] Slider update reflects instantly in Opportunity Matrix table
- [ ] Date filter to "last 30 days" changes heatmap (verify in console: fewer rows fetched)
- [ ] All filter resets return to default state correctly

---

## Acceptance Criteria

- [ ] `index.html` opens in Chrome without a local server (`file://` protocol) OR via `python3 -m http.server`
- [ ] Heatmap renders all 18 competitors × all 53 features (or filtered subset) with correct colors
- [ ] Clicking any heatmap cell navigates to Pain Points pre-filtered to that competitor + feature
- [ ] Supporting quotes visible inline in Pain Points (no extra click needed)
- [ ] Opportunity Matrix computes correctly from heatmap data
- [ ] Threshold slider updates Opportunity Matrix in real-time
- [ ] All empty states have user-facing messages (no blank sections)
- [ ] All Supabase errors surface as error banners (not silent console-only failures)
- [ ] URL hash reflects active tab; reloading restores correct tab
- [ ] No console errors on initial load or filter interactions

## Completion Checklist

- [ ] All 8 tasks implemented
- [ ] Manual test checklist 100% passed
- [ ] SQL cross-check values match dashboard values
- [ ] Pastel color scale is visually calm (not saturated traffic-light)
- [ ] No hardcoded competitor/feature lists outside the `COMPETITORS` / `FEATURES` constants
- [ ] `SUPABASE_URL` and `SUPABASE_ANON_KEY` constants are clearly marked `REPLACE_ME`
- [ ] No external CSS or JS dependencies beyond Supabase JS CDN

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `raw_mentions!inner` with subreddit `.eq()` filter returns 0 rows | Medium | High | Fall back to left-join `raw_mentions(subreddit)` and filter client-side |
| `.order('raw_mentions.score', ...)` not supported by Supabase JS v2 | Medium | Low | Sort client-side after fetching (safe with 350 rows) |
| 954 cells cause slow DOM render | Low | Medium | Table renders as innerHTML string (one reflow); should be <100ms |
| Sticky column + sticky header conflict on scroll | Medium | Medium | Use `z-index: 3` on top-left corner cell |

## Notes

- `index.html` is the only file changed. All schema, Python scripts, and GitHub Actions are untouched.
- The `SUPABASE_URL` and `SUPABASE_ANON_KEY` constants at the top of the file are the only deployment configuration needed — the PM pastes in their credentials and opens the file.
- Feature grouping (collapse related features in heatmap) is a "Could" item — implement only if heatmap renders clearly without it. With 53 features as column headers, horizontal scrolling is expected and acceptable.
- If `.select('competitor, feature, sentiment_score, raw_mentions!inner(subreddit)')` returns unexpected empty results without a subreddit filter, switch to `.select('competitor, feature, sentiment_score')` for the no-filter case and only add the join when a subreddit is selected.
