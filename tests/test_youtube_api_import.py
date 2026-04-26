import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ytsubs.db_schema import YouTubeDB
from ytsubs.scrape_channel_stats import ChannelStatsAPIUpdater
from ytsubs.scrape_videos import VideoAPIImporter
from ytsubs.youtube_api import parse_youtube_duration


class FakeYouTubeAPIClient:
    def __init__(self) -> None:
        self.recent_video_requests: list[tuple[str, str]] = []

    def iter_subscription_channel_ids(self, source_channel_id: str):
        self.source_channel_id = source_channel_id
        return iter(["UCAPI"])

    def list_channels(self, channel_ids):
        self.channel_ids = list(channel_ids)
        return [
            {
                "id": "UCAPI",
                "title": "API Channel",
                "url": "https://www.youtube.com/channel/UCAPI",
                "handle": "api-channel",
                "description": "From the API",
                "thumbnail_url": "https://img.example/channel.jpg",
                "subscriber_count": 12345,
                "is_verified": False,
            }
        ]

    def list_recent_channel_video_ids(self, channel_id: str, published_after: str):
        self.recent_video_requests.append((channel_id, published_after))
        return ["video1", "video2"]

    def list_videos(self, video_ids):
        self.video_ids = list(video_ids)
        return [
            {
                "id": "video1",
                "channel_id": "UCAPI",
                "title": "Fresh API Video",
                "url": "https://www.youtube.com/watch?v=video1",
                "thumbnail": "https://img.example/video1.jpg",
                "views": 2500,
                "published_date": "2026-04-25T12:00:00+00:00",
                "duration_text": "PT1H02M03S",
                "duration_seconds": 3723,
            },
            {
                "id": "video2",
                "channel_id": "UCAPI",
                "title": "Baseline Video",
                "url": "https://www.youtube.com/watch?v=video2",
                "thumbnail": "https://img.example/video2.jpg",
                "views": 1500,
                "published_date": "2026-04-24T12:00:00+00:00",
                "duration_text": "PT10M",
                "duration_seconds": 600,
            },
        ]


class YouTubeAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

        self.original_xdg_state_home = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = self.tmp_dir.name
        self.addCleanup(self._restore_xdg_state_home)

        db_path = Path(self.tmp_dir.name) / "ytsubs" / "youtube.db"
        self.db = YouTubeDB(db_path=str(db_path))
        self.addCleanup(self.db.close)

    def _restore_xdg_state_home(self) -> None:
        if self.original_xdg_state_home is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self.original_xdg_state_home

    def test_parse_youtube_duration_converts_iso_8601_duration(self) -> None:
        self.assertEqual(parse_youtube_duration("PT1H02M03S"), 3723)
        self.assertEqual(parse_youtube_duration("PT10M"), 600)

    def test_channel_updater_imports_public_subscription_channels(self) -> None:
        client = FakeYouTubeAPIClient()
        updater = ChannelStatsAPIUpdater(db=self.db, client=client)

        updated_count = updater.refresh_channels(subscription_source_channel_id="PUBLIC")

        row = self.db.db.execute("SELECT * FROM channels WHERE id = ?", ("UCAPI",)).fetchone()
        self.assertEqual(updated_count, 1)
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "API Channel")
        self.assertEqual(row["subscriber_count"], 12345)
        self.assertEqual(row["baseline_48h"], 2000)
        self.assertEqual(client.source_channel_id, "PUBLIC")

    def test_video_importer_fetches_recent_videos_for_tracked_channels(self) -> None:
        client = FakeYouTubeAPIClient()
        updater = ChannelStatsAPIUpdater(db=self.db, client=client)
        updater.refresh_channels(subscription_source_channel_id="PUBLIC")
        importer = VideoAPIImporter(db=self.db, client=client)

        result = importer.import_recent_videos(days=30)

        row = self.db.db.execute("SELECT * FROM videos WHERE id = ?", ("video1",)).fetchone()
        observation = self.db.db.execute(
            "SELECT * FROM video_observations WHERE video_id = ?",
            ("video1",),
        ).fetchone()
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["new"], 2)
        self.assertIsNotNone(row)
        self.assertEqual(row["channel_id"], "UCAPI")
        self.assertEqual(row["duration_seconds"], 3723)
        self.assertEqual(row["channel_resolution_method"], "youtube_api")
        self.assertIsNotNone(observation)


if __name__ == "__main__":
    unittest.main()
