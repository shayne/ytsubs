import unittest

from ytsubs.scrape_videos import VideoScraper


class VideoChannelCandidateTests(unittest.TestCase):
    def test_normalizes_all_dom_channel_candidates_before_metadata_fallback(self) -> None:
        candidates = VideoScraper.normalize_channel_candidates(
            {
                "channelCandidates": [
                    {
                        "channelId": "unfollowed",
                        "channelUrl": "https://www.youtube.com/@unfollowed",
                        "channelName": "Unfollowed Channel",
                    },
                    {
                        "channelId": "followed",
                        "channelUrl": "https://www.youtube.com/@followed",
                        "channelName": "Followed Channel",
                    },
                ],
                "channelText": "Unfollowed Channel and Followed Channel",
            }
        )

        self.assertEqual(
            candidates,
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
                {
                    "channel_id": None,
                    "channel_url": None,
                    "channel_name": "Unfollowed Channel and Followed Channel",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
