import json
import math
import sqlite3
import webbrowser
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from .db_schema import resolve_db_path, resolve_state_dir


def get_db(db_path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"Database connection error: {e}")
        raise


def check_db_initialized(db_path: Path) -> tuple[bool, str | None]:
    if not db_path.exists():
        return False, (
            "Database file not found. Run `ytsubs scrape-videos` or `ytsubs scrape-channels` first."
        )

    try:
        db = get_db(db_path)
        cursor = db.cursor()

        cursor.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table'
            """
        )
        existing_tables = {row[0] for row in cursor.fetchall()}
        required_tables = {"videos", "channels", "scrape_runs", "video_observations"}

        if not required_tables.issubset(existing_tables):
            missing_tables = required_tables - existing_tables
            return False, (
                f"Missing tables: {', '.join(sorted(missing_tables))}. "
                "Run `ytsubs scrape-videos` or `ytsubs scrape-channels` to initialize."
            )

        return True, None

    except sqlite3.Error as e:
        return False, f"Database error: {str(e)}"
    finally:
        if "db" in locals():
            db.close()


def get_thumbnail_url(video_id: str, thumbnail: str | None = None) -> str:
    """Get a valid thumbnail URL, falling back to YouTube's default if none provided."""
    if thumbnail and thumbnail.strip():
        return thumbnail
    return f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _age_curve_fraction_48h(age_hours: float) -> float:
    if age_hours <= 0:
        return 0.03
    if age_hours <= 8:
        return max(0.03, 0.6 * (age_hours / 8.0))
    if age_hours < 48:
        return 0.6 + 0.35 * ((age_hours - 8.0) / 40.0)
    return 0.95


def _age_curve_expected_slope(age_hours: float) -> float:
    if age_hours <= 0:
        return 0.075
    if age_hours <= 8:
        return 0.075
    if age_hours < 48:
        return 0.00875
    return 0.001


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _norm_ratio(value: float, cap: float = 6.0) -> float:
    if value <= 0:
        return 0.0
    return _clamp(math.log1p(value) / math.log1p(cap), 0.0, 1.0)


def _norm_reach(reach: float) -> float:
    if reach <= 0:
        return 0.0
    return _clamp(math.sqrt(reach * 10.0), 0.0, 1.0)


def _duration_prior(duration_seconds: int | None) -> float:
    if duration_seconds is None or duration_seconds <= 0:
        return 0.5
    if duration_seconds < 120:
        return 0.35
    if duration_seconds < 600:
        return 0.6
    if duration_seconds < 1800:
        return 1.0
    if duration_seconds < 3600:
        return 0.7
    return 0.5


def _freshness_relevance(age_hours: float) -> float:
    if age_hours <= 24:
        return 1.0
    if age_hours <= 72:
        return 0.95
    if age_hours <= 168:
        return 0.85
    if age_hours <= 336:
        return 0.72
    if age_hours <= 720:
        return 0.55
    return 0.45


def _velocity_weight(age_hours: float) -> float:
    if age_hours <= 24:
        return 0.25
    if age_hours <= 72:
        return 0.20
    if age_hours <= 168:
        return 0.12
    if age_hours <= 336:
        return 0.06
    return 0.02


def _confidence_multiplier(
    *,
    parse_confidence: float,
    channel_resolution_method: str | None,
    baseline_updated_at: datetime | None,
    subscriber_count: int,
    baseline_48h: float,
    now: datetime,
) -> float:
    confidence = _clamp(parse_confidence, 0.0, 1.0)
    multiplier = 0.75 + (0.30 * confidence)

    if baseline_updated_at is not None:
        staleness_days = (now - baseline_updated_at).total_seconds() / 86400.0
        if staleness_days > 45:
            multiplier -= 0.05

    if not subscriber_count or baseline_48h <= 0:
        multiplier -= 0.08

    if channel_resolution_method and channel_resolution_method not in {"direct", "channel_url", "feed_dom"}:
        multiplier -= 0.03

    return _clamp(multiplier, 0.75, 1.05)


def _early_breakout_boost(age_hours: float, relative_nowcast: float, velocity_shock: float) -> float:
    if age_hours > 6:
        return 0.0
    if relative_nowcast < 1.2 or velocity_shock < 1.2:
        return 0.0

    return _clamp(0.03 * math.log1p(relative_nowcast * velocity_shock), 0.0, 0.12)


def _load_rankable_rows(cursor: sqlite3.Cursor) -> list[sqlite3.Row]:
    cursor.execute(
        """
        WITH LatestObservation AS (
            SELECT
                video_id,
                views,
                age_hours,
                parse_confidence,
                observed_at
            FROM (
                SELECT
                    vo.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY vo.video_id
                        ORDER BY vo.observed_at DESC, vo.id DESC
                    ) AS rn
                FROM video_observations vo
            ) ranked
            WHERE rn = 1
        )
        SELECT
            v.id,
            v.title,
            v.url,
            v.thumbnail,
            v.duration_text,
            v.duration_seconds,
            v.published_date,
            v.views AS fallback_views,
            v.parse_confidence AS video_parse_confidence,
            v.channel_resolution_method,
            c.name AS channel_name,
            c.subscriber_count,
            c.is_verified,
            c.thumbnail_url AS channel_thumbnail,
            c.baseline_48h,
            c.baseline_updated_at,
            c.last_updated,
            lo.views AS observed_views,
            lo.age_hours AS observed_age_hours,
            lo.parse_confidence AS observation_parse_confidence,
            lo.observed_at
        FROM videos v
        JOIN channels c ON v.channel_id = c.id
        LEFT JOIN LatestObservation lo ON lo.video_id = v.id
        """
    )
    return cursor.fetchall()


def get_videos() -> list[dict[str, object]]:
    try:
        db_path = resolve_db_path()
        is_initialized, error_message = check_db_initialized(db_path)
        if not is_initialized:
            print(f"Error: {error_message}")
            return []

        db = get_db(db_path)
        cursor = db.cursor()
        rows = _load_rankable_rows(cursor)

        now = datetime.now(UTC)
        videos: list[dict[str, object]] = []

        for row in rows:
            published_dt = _parse_datetime(row["published_date"])
            if published_dt is None:
                print(f"Invalid date format for video {row['id']}: {row['published_date']}")
                continue

            observed_age = row["observed_age_hours"]
            if observed_age is not None and observed_age > 0:
                age_hours = float(observed_age)
            else:
                age_hours = max((now - published_dt).total_seconds() / 3600.0, 0.0)

            raw_views = row["observed_views"] if row["observed_views"] is not None else row["fallback_views"]
            current_views = int(raw_views or 0)

            baseline_48h = float(row["baseline_48h"] or 0)
            if baseline_48h <= 0:
                baseline_48h = float(max(current_views, 1))

            expected_fraction = _age_curve_fraction_48h(age_hours)
            expected_views_now = max(1.0, baseline_48h * expected_fraction)
            relative_nowcast = current_views / expected_views_now

            expected_slope = _age_curve_expected_slope(age_hours)
            expected_vph = max(1.0, baseline_48h * expected_slope)
            views_per_hour = current_views / max(age_hours, 1.0)
            velocity_shock = views_per_hour / expected_vph

            subscriber_count = int(row["subscriber_count"] or 0)
            subscriber_reach = current_views / max(subscriber_count, 1)

            predicted_48h = current_views if age_hours >= 48 else current_views * 0.95 / max(expected_fraction, 0.03)
            forecast_relative_performance = predicted_48h / max(baseline_48h, 1.0)

            duration_seconds = int(row["duration_seconds"]) if row["duration_seconds"] else None
            duration_prior = _duration_prior(duration_seconds)

            observation_confidence = row["observation_parse_confidence"]
            video_confidence = row["video_parse_confidence"]
            if observation_confidence is not None and video_confidence is not None:
                parse_confidence = min(float(observation_confidence), float(video_confidence))
            elif observation_confidence is not None:
                parse_confidence = float(observation_confidence)
            elif video_confidence is not None:
                parse_confidence = float(video_confidence)
            else:
                parse_confidence = 0.0

            confidence_multiplier = _confidence_multiplier(
                parse_confidence=parse_confidence,
                channel_resolution_method=row["channel_resolution_method"],
                baseline_updated_at=_parse_datetime(row["baseline_updated_at"]),
                subscriber_count=subscriber_count,
                baseline_48h=baseline_48h,
                now=now,
            )
            early_breakout_boost = _early_breakout_boost(age_hours, relative_nowcast, velocity_shock)
            freshness_relevance = _freshness_relevance(age_hours)
            velocity_weight = _velocity_weight(age_hours)

            base_score = (
                0.55 * _norm_ratio(relative_nowcast)
                + velocity_weight * _norm_ratio(velocity_shock)
                + 0.15 * _norm_reach(subscriber_reach)
                + 0.05 * duration_prior
            )
            performance_score = ((base_score * confidence_multiplier) + early_breakout_boost) * freshness_relevance

            video = {
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "thumbnail": get_thumbnail_url(row["id"], row["thumbnail"]),
                "views": current_views,
                "published_date": published_dt.isoformat(),
                "duration": row["duration_text"],
                "channel": {
                    "name": row["channel_name"],
                    "subscriber_count": subscriber_count,
                    "is_verified": bool(row["is_verified"]),
                    "thumbnail": row["channel_thumbnail"],
                    "average_views": int(baseline_48h),
                },
                "performance_score": float(performance_score),
                "performance_details": {
                    "relative_performance": float(relative_nowcast),
                    "subscriber_reach": float(subscriber_reach),
                    "forecast_relative_performance": float(forecast_relative_performance),
                    "velocity": float(views_per_hour),
                    "views_per_hour": float(views_per_hour),
                    "video_age_hours": float(age_hours),
                    "observed_fraction_48h": float(expected_fraction),
                    "predicted_views_48h": float(predicted_48h),
                    "velocity_shock": float(velocity_shock),
                    "confidence_multiplier": float(confidence_multiplier),
                    "parse_confidence": float(parse_confidence),
                    "expected_views_now": float(expected_views_now),
                    "early_breakout_boost": float(early_breakout_boost),
                    "freshness_relevance": float(freshness_relevance),
                    "velocity_weight": float(velocity_weight),
                },
            }

            videos.append(video)

        videos.sort(key=lambda v: (float(v["performance_score"]), str(v["published_date"])), reverse=True)

        db.close()
        return videos

    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []
    except Exception as e:
        print(f"Error: {e}")
        return []


def generate_html(videos: list[dict[str, object]], output_path: Path, open_browser: bool = True) -> None:
    template = (
        resources.files("ytsubs")
        .joinpath("static_template.html")
        .read_text(encoding="utf-8")
    )

    # Insert the video data as a JSON array
    video_data_json = json.dumps(videos)
    html = template.replace("const videoData = VIDEO_DATA_PLACEHOLDER;", f"const videoData = {video_data_json};")

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        f.write(html)

    # Get absolute file URL
    file_url = f"file://{output_path.resolve()}"

    print(f"\nGenerated static HTML file at: {output_path}")
    print(f"Found {len(videos)} videos")
    if videos:
        print(f"Date range: {videos[-1]['published_date']} to {videos[0]['published_date']}")
    if open_browser:
        print("\nOpening in default browser...")
        webbrowser.open(file_url)


def feed_path() -> Path:
    return resolve_state_dir() / "ytsubs_feed.html"


def open_feed() -> bool:
    target = feed_path()
    if not target.exists():
        print(f"Feed not found at: {target}")
        print("Run `ytsubs scrape-videos` to generate it.")
        return False

    file_url = f"file://{target.resolve()}"
    print(f"Opening feed: {target}")
    webbrowser.open(file_url)
    return True


def run(output_path: Path | None = None, open_browser: bool = True) -> None:
    print("Generating static YouTube feed page...")
    videos = get_videos()
    target = output_path or feed_path()
    generate_html(videos, output_path=target, open_browser=open_browser)
