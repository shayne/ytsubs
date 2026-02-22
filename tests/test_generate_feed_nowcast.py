import os
import sqlite3
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from ytsubs import generate_feed


class GenerateFeedNowcastTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

        self.original_xdg_state_home = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = self.tmp_dir.name
        self.addCleanup(self._restore_xdg_state_home)

        self.db_path = Path(self.tmp_dir.name) / "ytsubs" / "youtube.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        schema_path = Path(__file__).resolve().parents[1] / "src" / "ytsubs" / "schema.sql"
        schema_text = schema_path.read_text(encoding="utf-8")

        self.conn = sqlite3.connect(self.db_path)
        self.addCleanup(self.conn.close)
        self.conn.executescript(schema_text)
        self.conn.commit()

    def _restore_xdg_state_home(self) -> None:
        if self.original_xdg_state_home is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self.original_xdg_state_home

    def _insert_channel(
        self,
        channel_id: str,
        *,
        subscribers: int,
        baseline_48h: int,
        last_updated_hours_ago: int = 1,
    ) -> None:
        baseline_updated_at = (datetime.now(UTC) - timedelta(hours=last_updated_hours_ago)).isoformat()
        self.conn.execute(
            """
            INSERT INTO channels (
                id,
                youtube_id,
                name,
                url,
                handle,
                subscriber_count,
                baseline_48h,
                baseline_updated_at,
                last_updated
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                channel_id,
                channel_id,
                f"Channel {channel_id}",
                f"https://youtube.com/channel/{channel_id}",
                f"handle_{channel_id.lower()}",
                subscribers,
                baseline_48h,
                baseline_updated_at,
                baseline_updated_at,
            ),
        )

    def _insert_video(
        self,
        video_id: str,
        *,
        channel_id: str,
        published_hours_ago: int,
        parse_confidence: float = 1.0,
    ) -> None:
        published_date = (datetime.now(UTC) - timedelta(hours=published_hours_ago)).isoformat()
        self.conn.execute(
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
                parse_confidence,
                channel_resolution_method,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                f"Video {video_id}",
                f"https://youtube.com/watch?v={video_id}",
                channel_id,
                0,
                published_date,
                f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
                "12:34",
                754,
                parse_confidence,
                "feed_dom",
                datetime.now(UTC).isoformat(),
            ),
        )

    def _insert_observation(self, *, video_id: str, views: int, age_hours: float = 1.0) -> None:
        run_id = self.conn.execute(
            """
            INSERT INTO scrape_runs (mode, started_at, completed_at)
            VALUES ('videos', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        ).lastrowid
        self.conn.execute(
            """
            INSERT INTO video_observations (
                video_id,
                scrape_run_id,
                observed_at,
                views,
                age_hours,
                parse_confidence
            )
            VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, 1.0)
            """,
            (video_id, run_id, views, age_hours),
        )
        self.conn.commit()

    def test_breakout_video_can_outrank_older_high_view_video(self) -> None:
        self._insert_channel("UCBREAKOUT", subscribers=80_000, baseline_48h=20_000)
        self._insert_channel("UCSTEADY", subscribers=5_000_000, baseline_48h=350_000)

        self._insert_video("breakout", channel_id="UCBREAKOUT", published_hours_ago=2)
        self._insert_video("steady", channel_id="UCSTEADY", published_hours_ago=24)

        self._insert_observation(video_id="breakout", views=18_000)
        self._insert_observation(video_id="steady", views=280_000)

        videos = generate_feed.get_videos()
        self.assertGreaterEqual(len(videos), 2)
        self.assertEqual(videos[0]["id"], "breakout")
        self.assertIn("scores", videos[0])
        self.assertIn("core", videos[0]["scores"])
        self.assertIn("by_facet", videos[0]["scores"])
        self.assertIn("month", videos[0]["scores"]["by_facet"])

    def test_latest_observation_is_used_for_current_views(self) -> None:
        self._insert_channel("UCLATEST", subscribers=120_000, baseline_48h=40_000)
        self._insert_video("rolling", channel_id="UCLATEST", published_hours_ago=4)

        self._insert_observation(video_id="rolling", views=2_000)
        self._insert_observation(video_id="rolling", views=9_500)

        videos = generate_feed.get_videos()
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["id"], "rolling")
        self.assertEqual(videos[0]["views"], 9_500)

    def test_low_parse_confidence_gets_penalized(self) -> None:
        self._insert_channel("UCHIGH", subscribers=90_000, baseline_48h=30_000)
        self._insert_channel("UCLOW", subscribers=90_000, baseline_48h=30_000)

        self._insert_video("high-confidence", channel_id="UCHIGH", published_hours_ago=6, parse_confidence=1.0)
        self._insert_video("low-confidence", channel_id="UCLOW", published_hours_ago=6, parse_confidence=0.4)

        self._insert_observation(video_id="high-confidence", views=40_000)
        self._insert_observation(video_id="low-confidence", views=40_000)

        videos = generate_feed.get_videos()
        ids = [video["id"] for video in videos]
        self.assertEqual(ids[0], "high-confidence")

    def test_generate_feed_writes_unified_ranked_html(self) -> None:
        self._insert_channel("UCFEED", subscribers=110_000, baseline_48h=25_000)
        self._insert_video("feed-video", channel_id="UCFEED", published_hours_ago=3)
        self._insert_observation(video_id="feed-video", views=22_000)

        output_path = Path(self.tmp_dir.name) / "feed.html"
        generate_feed.run(output_path=output_path, open_browser=False)

        self.assertTrue(output_path.exists())
        html = output_path.read_text(encoding="utf-8")
        self.assertIn("feed-video", html)
        self.assertIn('"scores"', html)
        self.assertIn('"by_facet"', html)

    def test_strong_older_video_can_outrank_recent_video_within_month_window(self) -> None:
        self._insert_channel("UCRECENT", subscribers=300_000, baseline_48h=90_000)
        self._insert_channel("UCSTALE", subscribers=300_000, baseline_48h=90_000)

        # Decent recent breakout.
        self._insert_video("recent-breakout", channel_id="UCRECENT", published_hours_ago=18)
        self._insert_observation(video_id="recent-breakout", views=55_000, age_hours=18.0)

        # Older but much stronger month-window performance.
        self._insert_video("stale-outlier", channel_id="UCSTALE", published_hours_ago=696)
        self._insert_observation(video_id="stale-outlier", views=3_500_000, age_hours=696.0)

        videos = generate_feed.get_videos()
        ids = [video["id"] for video in videos]
        self.assertLess(ids.index("stale-outlier"), ids.index("recent-breakout"))


if __name__ == "__main__":
    unittest.main()
