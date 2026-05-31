# Vibecheck — Project Overview

Vibecheck is a competitive intelligence tool that monitors what people say about time-tracking software on Reddit. It surfaces patterns in how users praise or complain about specific tools, so the product team can identify gaps competitors are failing to fill.

---

## What it does

Every time the tool is run, it:

1. Searches Reddit for mentions of competitor tools across relevant communities
2. Saves the posts that meet a quality bar
3. Analyzes each post to extract what tool is being discussed and what feature is being praised or criticized
4. Scores the sentiment of each mention
5. Displays everything in a dashboard organized by tool and feature

---

## Data collection

**Where the data comes from**

Reddit posts from 15 communities including r/freelance, r/productivity, r/consulting, r/webdev, and r/timetracking.

**What is searched**

18 competitor tools: Toggl, Clockify, Harvest, Hubstaff, Timely, RescueTime, TimeCamp, Rize, Reclaim.ai, and others. The list is managed directly from the dashboard — anyone can add or pause a search term without touching the code.

**How often it runs**

Manually triggered for now. There is a planned button in the dashboard to kick off a collection run. Automatic daily scheduling will be added once the data volume is validated.

**What gets collected**

Only posts, not comments (comment collection is planned for a future release). Each search retrieves up to 100 posts per tool per community.

**Quality filters applied before saving**

- The post must have at least 5 upvotes and 2 replies (low-engagement posts tend to be spam or ignored opinions)
- The post body or title must contain an exact match for a competitor name — partial matches are excluded (for example, "Rize" will not match "capitalize")
- Deleted or removed posts are discarded

---

## Storage

Qualifying posts are saved to a database with the following information: the post title and body text combined, the community it came from, the score, the URL, and when it was posted on Reddit. Re-running the collection never creates duplicates — existing posts are simply updated in place.

---

## Analysis

Analysis runs after collection as a separate step.

**Step 1 — Extraction (AI)**

An AI model (Claude Haiku) reads batches of 25 posts and identifies: which competitor tool is being discussed, which specific feature is the subject of the discussion, and a short quote (under 20 words) that captures the key opinion. Posts with no clear competitor or feature signal are discarded at this stage.

**Step 2 — Sentiment scoring (local model)**

A lightweight language model (RoBERTa, runs on the machine without any API cost) reads each extracted quote and assigns a sentiment score between 0 and 1, plus a label: strongly negative, negative, negative or neutral, positive, or strongly positive.

---

## Dashboard

Four views:

- **Heatmap** — A grid of competitors versus features, colored red to green based on average sentiment. Shows at a glance where each tool is weakest. Filterable by date range and community.
- **Pain points** — Click any cell in the heatmap to see the individual posts behind that score, sorted by sentiment or Reddit engagement.
- **Opportunity matrix** — Features where every competitor scores below a chosen threshold. These are areas no tool is doing well — potential product opportunities.
- **Keyword manager** — Add, remove, or pause search terms. Changes take effect on the next collection run.

---

## Current status

- Database schema: complete
- Data collection (scraper): complete
- Analysis pipeline: not yet built
- Dashboard: not yet built
