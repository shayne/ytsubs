# Agent Instructions

## Environment

This project uses `uv` for Python package management. All Python commands should be run through `uv run` to ensure the correct virtual environment is used.

## Sanity Checks

Before finishing any changes, run the following checks:

### Type Checking

```bash
uv run uvx ty check
```

This runs the `ty` type checker with access to all installed dependencies.

### Running Scripts

To run any Python script:

```bash
uv run python <script.py>
```

## Project Structure

- `src/ytsubs/base_scraper.py` - Base class for Playwright-based scrapers
- `src/ytsubs/scrape_videos.py` - Scrapes subscription feed videos
- `src/ytsubs/scrape_channel_stats.py` - Scrapes channel statistics
- `src/ytsubs/db_schema.py` - SQLite database schema and operations
- `src/ytsubs/generate_feed.py` - Generates static HTML feed
- `src/ytsubs/cli.py` - CLI entry point and subcommands

## Makefile Commands

- `make clean` - Remove Python cache files
- `make reset-db` - Reset database to empty tables
- `make reset-videos` - Clear only the videos table
- `make reset-channels` - Clear only the channels table

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **Document remaining work** - Note anything that needs follow-up in the handoff
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
4. **Clean up** - Clear stashes, prune remote branches
5. **Verify** - All changes committed AND pushed
6. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
