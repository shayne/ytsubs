# Unified Nowcast Ranking Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current snapshot-weighted ranking with a unified ad-hoc-optimized nowcast ranking that surfaces early breakouts reliably.

**Architecture:** Move to a canonical channel ID schema, append-only scrape runs and video observations, and calculate point-in-time nowcast scores from latest observations with confidence and early-breakout modifiers. Keep a single feed ranking.

**Tech Stack:** Python 3.12+, SQLite, Playwright scraper, static Preact template.

---

### Task 1: Add breaking schema for runs, observations, and canonical channels

**Files:**
- Modify: `src/ytsubs/schema.sql`
- Modify: `src/ytsubs/db_schema.py`
- Test: `tests/test_generate_feed_nowcast.py`

### Task 2: Implement nowcast ranking in feed generation

**Files:**
- Modify: `src/ytsubs/generate_feed.py`
- Modify: `src/ytsubs/static_template.html`
- Test: `tests/test_generate_feed_nowcast.py`

### Task 3: Update scrapers to write new model data

**Files:**
- Modify: `src/ytsubs/scrape_channel_stats.py`
- Modify: `src/ytsubs/scrape_videos.py`
- Modify: `src/ytsubs/db_schema.py`
- Test: `tests/test_generate_feed_nowcast.py`

### Task 4: Validate end-to-end without browser

**Files:**
- Add: `tests/test_generate_feed_nowcast.py`
- Verify: `uv run python -m unittest tests/test_generate_feed_nowcast.py -v`
- Verify: `uv run uvx ty check`
