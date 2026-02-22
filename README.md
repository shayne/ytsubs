# YouTube subscriptions viewer

A local web app that displays your YouTube subscription videos sorted by a nowcast ranking algorithm tuned for ad hoc runs.

## Ranking Algorithm

Videos are ranked by a **core score** designed for ad hoc runs and then filtered by the selected facet window (2 days, 1 week, 2 weeks, 1 month). This means each facet shows the strongest videos **within that window**, not a globally recency-biased list.

Core score components:

1. **Nowcast vs Expected (55% weight)**
   - Compares current views to age-adjusted expected views from each channel's `baseline_48h`.

2. **Velocity Shock (20% weight)**
   - Compares current views/hour to expected slope at the video's current age.

3. **Subscriber Reach (15% weight)**
   - Views relative to subscriber count with diminishing returns.

4. **Duration Prior (5% weight)**
   - Lightweight bias for durations that tend to produce stable performance.

Score modifiers:
- **Confidence multiplier (0.75-1.05)** lowers rank impact for weak parse confidence/stale baselines.
- **Early breakout boost (up to +0.12)** helps very new videos that are simultaneously strong in nowcast and velocity.

Notes:
- Facet sorting uses facet score keys (`day`, `week`, `twoweeks`, `month`) backed by the core score.
- Freshness diagnostics are still recorded in details for transparency, but recency is not applied as a hard rank penalty across wider windows.

## Overview

A tool to track YouTube subscriptions and surface high-performing videos. It consists of:

1. A channel stats scraper that collects subscriber counts and a channel `baseline_48h` proxy
2. A video scraper that collects new videos from subscribed channels
3. Observation tracking per scrape run for better point-in-time ranking
4. A static page generator that creates a feed of videos sorted by performance

## Quick start (recommended: uvx)

You can run the tool directly with `uvx` — no cloning and no manual installs:

```bash
uvx ytsubs@latest scrape-channels
uvx ytsubs@latest scrape-videos
uvx ytsubs@latest open
```

## Setup (local dev)

1. Requirements:

   - Python 3.12+
   - Google Chrome browser

2. (Optional) Install tool versions with mise:

```bash
mise install
```

3. Install dependencies with uv:

```bash
uv sync
```

### Database reset

If local schema/data gets out of sync, reset your local DB:

```bash
rm -f ~/.local/state/ytsubs/youtube.db
```

## Usage

### First-time setup

Run these commands in order:

```bash
uv run ytsubs scrape-channels   # When Chrome opens, log in to YouTube
uv run ytsubs scrape-videos     # Collect recent videos and generate the feed
uv run ytsubs open              # Open the feed in your browser
```

Your YouTube login is saved in `~/.local/state/ytsubs/chrome_profile` (or `$XDG_STATE_HOME/ytsubs/chrome_profile`), so you'll only need to log in once. Subsequent runs will reuse this profile.

### Regular usage

1. Collect video data:

```bash
uv run ytsubs scrape-videos  # Run daily to get new videos
```

2. Update channel statistics (subscriber counts and baseline views):

```bash
uv run ytsubs scrape-channels  # Run occasionally (e.g., monthly)
```

3. Open the feed:

```bash
uv run ytsubs open  # Opens the latest feed
```

The feed is written to `~/.local/state/ytsubs/ytsubs_feed.html` (or `$XDG_STATE_HOME/ytsubs/ytsubs_feed.html`).

### Data locations (XDG)

- Chrome profile: `~/.local/state/ytsubs/chrome_profile` (or `$XDG_STATE_HOME/ytsubs/chrome_profile`)
- SQLite DB: `~/.local/state/ytsubs/youtube.db` (or `$XDG_STATE_HOME/ytsubs/youtube.db`)
- Feed output: `~/.local/state/ytsubs/ytsubs_feed.html` (or `$XDG_STATE_HOME/ytsubs/ytsubs_feed.html`)

### Debug tooling

```bash
uv run ytsubs debug-scrape --scrolls 4 --filter "gymkhana"
```

## Development

The project uses:

- SQLite for data storage
- Playwright for web scraping
- Preact for the frontend (served statically)

## Makefile commands

The project includes several helpful make commands:

- `make clean`: Remove Python cache files
- `make reset-db`: Reset database to empty tables
- `make reset-videos`: Clear only the videos table
- `make reset-channels`: Clear only the channels table

## Requirements

- Python 3.12+
- Google Chrome
