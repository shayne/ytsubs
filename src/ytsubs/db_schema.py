import os
import re
import sqlite3
from importlib import resources
from pathlib import Path
from typing import Any


def resolve_state_dir() -> Path:
    state_root = Path(
        os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
    )
    target = state_root / "ytsubs"
    target.mkdir(parents=True, exist_ok=True)
    return target


def resolve_db_path(db_path: str | None = None) -> Path:
    if db_path:
        return Path(db_path).expanduser()

    return resolve_state_dir() / "youtube.db"


class YouTubeDB:
    db: sqlite3.Connection

    def __init__(self, db_path: str | None = None):
        self.db_path = resolve_db_path(db_path)
        self.setup_database()

    def setup_database(self) -> None:
        """Initialize SQLite database and rebuild legacy schemas if needed."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        cursor = self.db.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}
        expected_tables = {"channels", "videos", "scrape_runs", "video_observations"}

        if not expected_tables.issubset(existing_tables):
            self._install_schema(cursor)
            self.db.commit()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            existing_tables = {row[0] for row in cursor.fetchall()}

        if not expected_tables.issubset(existing_tables):
            raise RuntimeError("Database schema initialization failed: required tables are missing.")

        if self._is_legacy_schema(cursor):
            print("Detected legacy ytsubs schema. Rebuilding database for nowcast ranking model...")
            self._rebuild_database(cursor)
            self.db.commit()

    def _install_schema(self, cursor: sqlite3.Cursor) -> None:
        schema_text = (
            resources.files("ytsubs")
            .joinpath("schema.sql")
            .read_text(encoding="utf-8")
        )
        cursor.executescript(schema_text)

    def _rebuild_database(self, cursor: sqlite3.Cursor) -> None:
        cursor.executescript(
            """
            DROP TABLE IF EXISTS video_observations;
            DROP TABLE IF EXISTS scrape_runs;
            DROP TABLE IF EXISTS videos;
            DROP TABLE IF EXISTS channels;
            """
        )
        self._install_schema(cursor)

    @staticmethod
    def _table_columns(cursor: sqlite3.Cursor, table_name: str) -> set[str]:
        cursor.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in cursor.fetchall()}

    def _is_legacy_schema(self, cursor: sqlite3.Cursor) -> bool:
        channels_columns = self._table_columns(cursor, "channels")
        videos_columns = self._table_columns(cursor, "videos")

        required_channel_columns = {"baseline_48h", "baseline_updated_at"}
        required_video_columns = {
            "duration_text",
            "duration_seconds",
            "last_seen_at",
            "parse_confidence",
            "channel_resolution_method",
        }

        return not required_channel_columns.issubset(channels_columns) or not required_video_columns.issubset(videos_columns)

    def get_last_video_date(self) -> sqlite3.Row | None:
        """Get the most recent video date from the database."""
        cursor = self.db.cursor()
        cursor.execute(
            """
            SELECT published_date
            FROM videos
            ORDER BY published_date DESC
            LIMIT 1
            """
        )
        return cursor.fetchone()

    def get_tracked_channels(self) -> list[sqlite3.Row]:
        """Get list of tracked channels."""
        cursor = self.db.cursor()
        cursor.execute("SELECT id, url FROM channels")
        return cursor.fetchall()

    def update_channel_subscriber_count(self, channel_id: str, sub_count: int) -> None:
        """Update subscriber count for a channel."""
        cursor = self.db.cursor()
        cursor.execute(
            """
            UPDATE channels
            SET subscriber_count = ?, last_updated = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (sub_count, channel_id),
        )
        self.db.commit()

    def upsert_channel(
        self,
        *,
        channel_id: str,
        youtube_id: str,
        name: str,
        url: str,
        handle: str | None,
        subscriber_count: int,
        description: str,
        thumbnail_url: str | None,
        is_verified: bool,
        baseline_48h: int,
    ) -> None:
        cursor = self.db.cursor()
        cursor.execute(
            """
            INSERT INTO channels (
                id,
                youtube_id,
                name,
                url,
                handle,
                description,
                thumbnail_url,
                subscriber_count,
                is_verified,
                baseline_48h,
                baseline_updated_at,
                last_updated
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                youtube_id = excluded.youtube_id,
                name = excluded.name,
                url = excluded.url,
                handle = excluded.handle,
                description = excluded.description,
                thumbnail_url = excluded.thumbnail_url,
                subscriber_count = excluded.subscriber_count,
                is_verified = excluded.is_verified,
                baseline_48h = excluded.baseline_48h,
                baseline_updated_at = CURRENT_TIMESTAMP,
                last_updated = CURRENT_TIMESTAMP
            """,
            (
                channel_id,
                youtube_id,
                name,
                url,
                handle,
                description,
                thumbnail_url,
                subscriber_count,
                int(is_verified),
                baseline_48h,
            ),
        )
        self.db.commit()

    def start_scrape_run(self, mode: str) -> int:
        cursor = self.db.cursor()
        cursor.execute(
            """
            INSERT INTO scrape_runs (mode, started_at)
            VALUES (?, CURRENT_TIMESTAMP)
            """,
            (mode,),
        )
        self.db.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("Failed to create scrape run ID")
        return int(cursor.lastrowid)

    def finish_scrape_run(self, scrape_run_id: int) -> None:
        cursor = self.db.cursor()
        cursor.execute(
            """
            UPDATE scrape_runs
            SET completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (scrape_run_id,),
        )
        self.db.commit()

    @staticmethod
    def _split_collab_channel_name(channel_name: str) -> list[str]:
        pieces = re.split(
            r"\s+(?:and|x|feat\.?|ft\.?)\s+|\s*&\s*|[,/|+]",
            channel_name,
            flags=re.IGNORECASE,
        )
        seen: set[str] = set()
        tokens: list[str] = []
        for piece in pieces:
            token = piece.strip().strip("-").strip()
            if len(token) < 2:
                continue
            token_lower = token.lower()
            if token_lower in seen:
                continue
            seen.add(token_lower)
            tokens.append(token)
        return tokens

    def _resolve_channel_by_name_or_handle(self, cursor: sqlite3.Cursor, candidate: str) -> str | None:
        normalized = candidate.strip()
        if not normalized:
            return None
        handle_candidate = normalized.lstrip("@")
        cursor.execute(
            """
            SELECT id
            FROM channels
            WHERE LOWER(handle) = LOWER(?) OR LOWER(name) = LOWER(?)
            LIMIT 2
            """,
            (handle_candidate, normalized),
        )
        matches = cursor.fetchall()
        if len(matches) == 1:
            return str(matches[0][0])
        return None

    def resolve_channel_id(
        self,
        channel_identifier: str | None,
        channel_url: str | None,
        channel_name: str | None = None,
    ) -> tuple[str | None, str]:
        """Resolve any channel identifier (ID or handle) to canonical channels.id."""
        candidates: list[tuple[str, str]] = []

        if channel_identifier:
            normalized = channel_identifier.strip().lstrip("@")
            if normalized:
                candidates.append((normalized, "direct"))

        if channel_url:
            handle_match = re.search(r"/@([A-Za-z0-9_-]+)", channel_url)
            id_match = re.search(r"/channel/([A-Za-z0-9_-]+)", channel_url)
            if id_match:
                candidates.append((id_match.group(1), "channel_url"))
            if handle_match:
                candidates.append((handle_match.group(1), "handle_url"))

        cursor = self.db.cursor()
        for candidate, source in candidates:
            cursor.execute(
                """
                SELECT id
                FROM channels
                WHERE
                    id = ?
                    OR youtube_id = ?
                    OR handle = ?
                    OR LOWER(handle) = LOWER(?)
                LIMIT 1
                """,
                (candidate, candidate, candidate, candidate),
            )
            row = cursor.fetchone()
            if row:
                return str(row[0]), source

        if channel_name:
            normalized_name = channel_name.strip()
            if normalized_name:
                resolved = self._resolve_channel_by_name_or_handle(cursor, normalized_name)
                if resolved:
                    return resolved, "channel_name"

                for handle in re.findall(r"@([A-Za-z0-9_-]+)", normalized_name):
                    resolved = self._resolve_channel_by_name_or_handle(cursor, handle)
                    if resolved:
                        return resolved, "channel_name_handle"

                for token in self._split_collab_channel_name(normalized_name):
                    resolved = self._resolve_channel_by_name_or_handle(cursor, token)
                    if resolved:
                        return resolved, "channel_name_token"

        return None, "unresolved"

    @staticmethod
    def extract_video_id(video_url: str | None) -> str | None:
        if not video_url:
            return None

        query_match = re.search(r"[?&]v=([A-Za-z0-9_-]+)", video_url)
        if query_match:
            return query_match.group(1)

        short_match = re.search(r"youtu\.be/([A-Za-z0-9_-]+)", video_url)
        if short_match:
            return short_match.group(1)

        return None

    def upsert_video(
        self,
        *,
        video_id: str,
        channel_id: str,
        title: str,
        url: str,
        thumbnail: str | None,
        views: int,
        published_date: str | None,
        duration_text: str | None,
        duration_seconds: int | None,
        parse_confidence: float,
        channel_resolution_method: str,
    ) -> None:
        cursor = self.db.cursor()
        cursor.execute(
            """
            INSERT INTO videos (
                id,
                title,
                url,
                channel_id,
                views,
                published_date,
                thumbnail,
                duration_text,
                duration_seconds,
                last_seen_at,
                parse_confidence,
                channel_resolution_method
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                url = excluded.url,
                channel_id = excluded.channel_id,
                views = excluded.views,
                published_date = excluded.published_date,
                thumbnail = excluded.thumbnail,
                duration_text = COALESCE(NULLIF(excluded.duration_text, ''), videos.duration_text),
                duration_seconds = CASE
                    WHEN NULLIF(excluded.duration_text, '') IS NULL THEN videos.duration_seconds
                    ELSE excluded.duration_seconds
                END,
                last_seen_at = CURRENT_TIMESTAMP,
                parse_confidence = excluded.parse_confidence,
                channel_resolution_method = excluded.channel_resolution_method
            """,
            (
                video_id,
                title,
                url,
                channel_id,
                views,
                published_date,
                thumbnail,
                duration_text,
                duration_seconds,
                parse_confidence,
                channel_resolution_method,
            ),
        )
        self.db.commit()

    def record_video_observation(
        self,
        *,
        video_id: str,
        scrape_run_id: int,
        views: int,
        age_hours: float | None,
        parse_confidence: float,
    ) -> None:
        cursor = self.db.cursor()
        cursor.execute(
            """
            INSERT INTO video_observations (
                video_id,
                scrape_run_id,
                observed_at,
                views,
                age_hours,
                parse_confidence
            )
            VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
            ON CONFLICT(video_id, scrape_run_id) DO UPDATE SET
                views = excluded.views,
                age_hours = excluded.age_hours,
                parse_confidence = excluded.parse_confidence,
                observed_at = CURRENT_TIMESTAMP
            """,
            (video_id, scrape_run_id, views, age_hours, parse_confidence),
        )
        self.db.commit()

    def update_video_and_channel(self, video_info: dict[str, Any]) -> None:
        """Backwards-compatible helper for older call-sites."""
        channel_identifier = video_info.get("channel_id")
        channel_url = video_info.get("channel_url")
        channel_id, resolution_method = self.resolve_channel_id(
            channel_identifier,
            channel_url,
            video_info.get("channel_name"),
        )

        if not channel_id:
            print(f"Could not resolve channel for video: {video_info.get('url')}")
            return

        video_id = self.extract_video_id(video_info.get("url"))
        if not video_id:
            print(f"Could not extract video ID from URL: {video_info.get('url')}")
            return

        self.upsert_video(
            video_id=video_id,
            channel_id=channel_id,
            title=video_info.get("title", ""),
            url=video_info.get("url", ""),
            thumbnail=video_info.get("thumbnail"),
            views=int(video_info.get("views", 0)),
            published_date=video_info.get("publish_date"),
            duration_text=video_info.get("duration_text"),
            duration_seconds=video_info.get("duration_seconds"),
            parse_confidence=float(video_info.get("parse_confidence", 1.0)),
            channel_resolution_method=resolution_method,
        )

    def close(self) -> None:
        """Close the database connection."""
        if self.db:
            self.db.close()
