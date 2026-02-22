PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    youtube_id TEXT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    handle TEXT UNIQUE,
    description TEXT,
    subscriber_count INTEGER DEFAULT 0,
    thumbnail_url TEXT,
    is_verified BOOLEAN DEFAULT 0,
    baseline_48h INTEGER DEFAULT 0,
    baseline_updated_at TIMESTAMP,
    last_updated TIMESTAMP
);

CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    views INTEGER DEFAULT 0,
    published_date TIMESTAMP,
    thumbnail TEXT,
    duration_text TEXT,
    duration_seconds INTEGER,
    discovered_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP,
    parse_confidence REAL DEFAULT 1.0,
    channel_resolution_method TEXT,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS video_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    scrape_run_id INTEGER NOT NULL,
    observed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    views INTEGER NOT NULL,
    age_hours REAL,
    parse_confidence REAL DEFAULT 1.0,
    FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
    FOREIGN KEY (scrape_run_id) REFERENCES scrape_runs(id) ON DELETE CASCADE,
    UNIQUE(video_id, scrape_run_id)
);

CREATE INDEX IF NOT EXISTS idx_channels_handle ON channels(handle);
CREATE INDEX IF NOT EXISTS idx_videos_published_date ON videos(published_date);
CREATE INDEX IF NOT EXISTS idx_videos_channel_id ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_last_seen_at ON videos(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_observations_video_id_observed_at ON video_observations(video_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_observations_scrape_run_id ON video_observations(scrape_run_id);
