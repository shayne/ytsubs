import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ytsubs.db_schema import YouTubeDB


class VideoUpsertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

        self.original_xdg_state_home = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = self.tmp_dir.name
        self.addCleanup(self._restore_xdg_state_home)

        db_path = Path(self.tmp_dir.name) / "ytsubs" / "youtube.db"
        self.db = YouTubeDB(db_path=str(db_path))
        self.addCleanup(self.db.close)
        self._insert_channel("UC123")

    def _restore_xdg_state_home(self) -> None:
        if self.original_xdg_state_home is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self.original_xdg_state_home

    def _insert_channel(self, channel_id: str) -> None:
        self.db.db.execute(
            """
            INSERT INTO channels (
                id,
                youtube_id,
                name,
                url,
                handle,
                subscriber_count,
                baseline_48h,
                last_updated
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                channel_id,
                channel_id,
                "Example Channel",
                "https://youtube.com/@Example",
                "Example",
                1000,
                100,
            ),
        )
        self.db.db.commit()

    def _upsert_video(
        self,
        duration_text: str | None,
        duration_seconds: int | None,
        thumbnail: str | None = "https://i.ytimg.com/vi/video123/maxresdefault.jpg",
    ) -> None:
        self.db.upsert_video(
            video_id="video123",
            channel_id="UC123",
            title="Example Video",
            url="https://www.youtube.com/watch?v=video123",
            thumbnail=thumbnail,
            views=1234,
            published_date="2026-04-22T12:00:00+00:00",
            duration_text=duration_text,
            duration_seconds=duration_seconds,
            parse_confidence=0.95,
            channel_resolution_method="channel_id",
        )

    def test_null_duration_update_preserves_existing_duration(self) -> None:
        self._upsert_video("12:34", 754)

        self._upsert_video(None, None)

        row = self.db.db.execute(
            "SELECT duration_text, duration_seconds FROM videos WHERE id = ?",
            ("video123",),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["duration_text"], "12:34")
        self.assertEqual(row["duration_seconds"], 754)

    def test_null_thumbnail_update_preserves_existing_thumbnail(self) -> None:
        original_thumbnail = "https://i.ytimg.com/vi/video123/maxresdefault.jpg"
        self._upsert_video("12:34", 754, thumbnail=original_thumbnail)

        self._upsert_video("12:34", 754, thumbnail=None)

        row = self.db.db.execute(
            "SELECT thumbnail FROM videos WHERE id = ?",
            ("video123",),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["thumbnail"], original_thumbnail)


if __name__ == "__main__":
    unittest.main()
