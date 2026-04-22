from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class YouTubeDurationSelectorTests(unittest.TestCase):
    def test_production_scraper_includes_current_thumbnail_badge_selector(self) -> None:
        source = (ROOT / "src" / "ytsubs" / "scrape_videos.py").read_text()

        self.assertIn("yt-thumbnail-bottom-overlay-view-model .ytBadgeShapeText", source)

    def test_debug_scraper_includes_current_thumbnail_badge_selector(self) -> None:
        source = (ROOT / "src" / "ytsubs" / "debug_scrape.py").read_text()

        self.assertIn("yt-thumbnail-bottom-overlay-view-model .ytBadgeShapeText", source)


if __name__ == "__main__":
    unittest.main()
