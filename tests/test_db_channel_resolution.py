import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ytsubs.db_schema import YouTubeDB


class ChannelResolutionTests(unittest.TestCase):
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

    def _insert_channel(self, channel_id: str, name: str, handle: str) -> None:
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
                name,
                f"https://youtube.com/@{handle}",
                handle,
                1000,
                100,
            ),
        )
        self.db.db.commit()

    def test_resolve_collab_name_to_tracked_channel_token(self) -> None:
        self._insert_channel("UCFOOD", "Insider Food", "InsiderFood")

        resolved_id, method = self.db.resolve_channel_id(
            channel_identifier=None,
            channel_url=None,
            channel_name="Insider and Insider Food",
        )

        self.assertEqual(resolved_id, "UCFOOD")
        self.assertEqual(method, "channel_name_token")

    def test_resolve_channel_candidates_accepts_any_tracked_collab_channel(self) -> None:
        self._insert_channel("UCFOLLOWED", "Followed Channel", "followed")

        resolved_id, method = self.db.resolve_channel_candidates(
            [
                {
                    "channel_id": "unfollowed",
                    "channel_url": "https://www.youtube.com/@unfollowed",
                    "channel_name": "Unfollowed Channel",
                },
                {
                    "channel_id": "followed",
                    "channel_url": "https://www.youtube.com/@followed",
                    "channel_name": "Followed Channel",
                },
            ]
        )

        self.assertEqual(resolved_id, "UCFOLLOWED")
        self.assertEqual(method, "candidate_2+direct")


if __name__ == "__main__":
    unittest.main()
