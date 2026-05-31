# Supabase Client Libraries — Reference

Sources:
- https://github.com/supabase/supabase-py
- https://supabase.com/docs/reference/python/introduction
- https://supabase.com/docs/reference/javascript/introduction
- https://supabase.com/docs/reference/javascript/installing  
Researched: 2026-05-30

---

## Python client (`supabase-py`)

### Initialization

```python
from supabase import create_client

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
# Use SUPABASE_SERVICE_KEY for server-side scripts (bypasses RLS)
# Use SUPABASE_KEY (anon) only when RLS policies permit the operation
```

### Insert

```python
# Single row
response = supabase.table("raw_mentions").insert({"id": "abc", "body": "..."}).execute()

# Bulk insert
response = supabase.table("raw_mentions").insert([
    {"id": "abc", "body": "..."},
    {"id": "def", "body": "..."},
]).execute()

# Insert and return the inserted row
response = supabase.table("raw_mentions").insert({"id": "abc"}).select("id, body").execute()
```

### Upsert

```python
# Single row upsert — on_conflict specifies the unique column(s)
response = (
    supabase.table("raw_mentions")
    .upsert(
        {"id": "abc123", "body": "some text", "score": 10},
        on_conflict="id",
    )
    .execute()
)

# Bulk upsert
response = (
    supabase.table("raw_mentions")
    .upsert(
        [
            {"id": "abc123", "body": "text a", "score": 5},
            {"id": "def456", "body": "text b", "score": 8},
        ],
        on_conflict="id",
    )
    .execute()
)
```

`on_conflict` must match a UNIQUE constraint on the table. Existing rows matching the
conflict column are updated; new rows are inserted.

### Select with filters

All filters are chained before `.execute()`. The chain is lazy until `.execute()` is called.

```python
# All rows
response = supabase.table("mention_analyses").select("*").execute()

# Select specific columns
response = supabase.table("mention_analyses").select("mention_id, competitor, sentiment_score").execute()

# eq — equality filter
response = (
    supabase.table("mention_analyses")
    .select("*")
    .eq("competitor", "Toggl")
    .execute()
)

# gt — greater than
response = (
    supabase.table("mention_analyses")
    .select("*")
    .gt("sentiment_score", 0.7)
    .execute()
)

# lt — less than
response = (
    supabase.table("mention_analyses")
    .select("*")
    .lt("sentiment_score", 0.4)
    .execute()
)

# ilike — case-insensitive pattern match (% is wildcard)
response = (
    supabase.table("raw_mentions")
    .select("*")
    .ilike("body", "%toggl%")
    .execute()
)

# Chain multiple filters (AND semantics by default)
response = (
    supabase.table("mention_analyses")
    .select("*")
    .eq("competitor", "Toggl")
    .lt("sentiment_score", 0.45)
    .execute()
)

# Join related table
response = (
    supabase.table("mention_analyses")
    .select("*, raw_mentions(subreddit, score)")
    .eq("competitor", "Toggl")
    .execute()
)
```

### Accessing results

```python
response = supabase.table("mention_analyses").select("*").execute()
rows = response.data          # list of dicts
count = response.count        # None unless count="exact" passed to select()
```

---

## JavaScript client (`@supabase/supabase-js`)

### Browser usage — no Node, no build step

Load via CDN (jsDelivr or unpkg — both are official options):

```html
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
```

or

```html
<script src="https://unpkg.com/@supabase/supabase-js@2"></script>
```

After the script tag, `supabase` is available on `window.supabase`:

```html
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<script>
  const { createClient } = supabase

  const SUPABASE_URL = 'https://your-project-ref.supabase.co'
  const SUPABASE_ANON_KEY = 'your-anon-key'   // safe in browser — RLS enforced

  const client = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)
</script>
```

This is exactly the pattern used in `index.html` — `SUPABASE_URL` and `SUPABASE_KEY`
defined as JS constants at the top of the file, anon key only.

### Select with filters

```js
// All rows
const { data, error } = await client.from('mention_analyses').select('*')

// Specific columns
const { data } = await client
  .from('mention_analyses')
  .select('competitor, feature, sentiment_score')

// eq filter
const { data } = await client
  .from('mention_analyses')
  .select('*')
  .eq('competitor', 'Toggl')

// gt / lt
const { data } = await client
  .from('mention_analyses')
  .select('*')
  .gt('sentiment_score', 0.7)

const { data } = await client
  .from('mention_analyses')
  .select('*')
  .lt('sentiment_score', 0.45)

// ilike — case-insensitive pattern
const { data } = await client
  .from('raw_mentions')
  .select('*')
  .ilike('body', '%toggl%')

// Chain filters (AND)
const { data } = await client
  .from('mention_analyses')
  .select('*')
  .eq('competitor', 'Toggl')
  .lt('sentiment_score', 0.45)
  .order('sentiment_score', { ascending: true })
  .range(0, 49)   // pagination — first 50 rows

// Join related table
const { data } = await client
  .from('mention_analyses')
  .select('*, raw_mentions(subreddit, score)')
  .eq('competitor', 'Toggl')
```

### Upsert

```js
// Single row
const { data, error } = await client
  .from('raw_mentions')
  .upsert({ id: 'abc123', body: 'some text', score: 10 }, { onConflict: 'id' })

// Bulk upsert
const { data, error } = await client
  .from('raw_mentions')
  .upsert(
    [
      { id: 'abc123', body: 'text a', score: 5 },
      { id: 'def456', body: 'text b', score: 8 },
    ],
    { onConflict: 'id' }
  )

// Upsert and return the resulting rows
const { data, error } = await client
  .from('raw_mentions')
  .upsert({ id: 'abc123', body: 'text' }, { onConflict: 'id' })
  .select()
```

By default, upserted rows are **not returned** — chain `.select()` to get them back.

### Error handling pattern

```js
const { data, error } = await client
  .from('mention_analyses')
  .select('*')
  .eq('competitor', 'Toggl')

if (error) {
  console.error('Supabase error:', error.message)
  return
}
// use data
```

---

## RLS notes (relevant to this project)

- **Dashboard (`index.html`)**: uses anon key → subject to RLS. Tables must grant `SELECT` to `anon` role.
- **Python scripts (`scraper.py`, `analyzer.py`)**: use service role key → bypasses RLS. Keep this key server-side only (GitHub Actions secrets, never in `index.html`).
- **`search_terms` table**: grants anon `INSERT`/`UPDATE` so the Keyword Manager tab works from the browser.
