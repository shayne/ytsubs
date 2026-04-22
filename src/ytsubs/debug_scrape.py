"""
Minimal debugger for the subscriptions feed scraping path.

Goals:
1) Collect the raw DOM-derived info that scrape_videos.py relies on.
2) Highlight items that are likely to be problematic (missing channelId, multiple channel links).
3) Allow quick ad‑hoc filtering of titles without hard‑coding examples.

Usage:
    uv run ytsubs debug-scrape --scrolls 4 --filter "gymkhana"

Notes for future debugging (LLM-friendly):
- The page.evaluate snippet mirrors the selectors used in scrape_videos.py.
- Collaboration cards often have no inline channel link; check `channelLinks` and `data*` fields.
- If channel info is missing, try oEmbed in scrape_videos.py (already implemented there).
"""

import argparse
import json
from collections import OrderedDict
from typing import Dict, Any

from .base_scraper import BaseScraper


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


class DebugVideoScraper(BaseScraper):
    def __init__(self, debug: bool = False, scrolls: int = 6, title_filter: str | None = None):
        super().__init__(debug)
        self.scrolls = scrolls
        self.title_filter = title_filter.lower() if title_filter else None

    def scrape(self):
        print("Opening subscriptions feed…")
        self.page.goto("https://www.youtube.com/feed/subscriptions")
        self.wait_for_page_load(3)

        seen_ids = OrderedDict()

        for i in range(self.scrolls):
            print(f"Collecting batch {i + 1}/{self.scrolls}…")
            batch = self.page.evaluate(self._collect_script())

            for video in batch:
                vid = video.get("videoId") or video.get("url")
                if not vid:
                    continue
                if vid not in seen_ids:
                    seen_ids[vid] = video
            self.scroll_page()

        print(f"Collected {len(seen_ids)} unique items")

        # Log any items that have multiple channel links or missing channel IDs
        multi_channel = [v for v in seen_ids.values() if v.get("channelLinks") and len(v["channelLinks"]) > 1]
        missing_channel = [v for v in seen_ids.values() if not v.get("channelId")]

        print("\n=== Multi-channel link candidates ===")
        for v in multi_channel:
            print(self._summarize(v))

        print("\n=== Missing channelId ===")
        for v in missing_channel:
            print(self._summarize(v))

        # Optional title filter for ad‑hoc investigations
        if self.title_filter:
            target = [v for v in seen_ids.values() if self.title_filter in (v.get("title") or "").lower()]
            print(f"\n=== Title matches for '{self.title_filter}' ===")
            if not target:
                print(f"No titles containing '{self.title_filter}' found in collected feed")
            for v in target:
                print(pretty(v))

    def _summarize(self, video: Dict[str, Any]) -> str:
        parts = [
            f"title={video.get('title')}",
            f"id={video.get('videoId')}",
            f"channelId={video.get('channelId')}",
            f"channelLinks={[c.get('text') for c in video.get('channelLinks', [])]}",
            f"publishRaw={video.get('publishText')}",
            f"viewsRaw={video.get('viewsText')}",
            f"duration={video.get('duration')}",
        ]
        return " | ".join(parts)

    def _collect_script(self) -> str:
        # Returned string evaluated in the page to avoid Python f-string escapes
        return r"""
        () => {
          const selectors = [
            'ytd-rich-item-renderer:not([is-slim-media])',
            'ytd-rich-grid-media',
            'ytd-grid-video-renderer',
            '#contents > ytd-rich-item-renderer'
          ];
          let elements = [];
          for (const selector of selectors) {
            elements = Array.from(document.querySelectorAll(selector));
            if (elements.length) break;
          }

          const collectFromData = (el) => {
            const data = el.data || el.__data || el.__internalElement?.data || {};
            const vr = data?.data?.richItemRenderer?.content?.videoRenderer
                      || data?.data?.videoRenderer
                      || data?.richItemRenderer?.content?.videoRenderer
                      || data?.videoRenderer;
            const ownerRuns = vr?.ownerText?.runs || vr?.longBylineText?.runs || [];
            const browse = ownerRuns[0]?.navigationEndpoint?.browseEndpoint;
            return {
              channelId: vr?.channelId || browse?.browseId,
              channelUrlFromData: browse?.canonicalBaseUrl,
              channelNameFromData: ownerRuns.map(r => r.text).join(''),
              videoIdFromData: vr?.videoId,
            };
          };

          return elements.map(el => {
            const titleEl = el.querySelector('h3 a') ||
                            el.querySelector('a#video-title-link') ||
                            el.querySelector('#video-title');
            const metadataSpans = el.querySelectorAll('yt-lockup-metadata-view-model span');
            const channelAnchors = Array.from(el.querySelectorAll('a')).filter(a => {
              const href = a.href || '';
              return href.includes('/@') || href.includes('/channel/');
            }).map(a => ({ text: a.textContent.trim(), href: a.href }));

            let publishText = null;
            let viewsText = null;
            metadataSpans.forEach(span => {
              const text = span.textContent.trim();
              if (text.includes('views')) viewsText = text;
              if (/(ago|hour|day|week|month|year)/.test(text)) publishText = text;
            });

            const dataBits = collectFromData(el);

            const durationCandidates = [
              el.querySelector('yt-thumbnail-bottom-overlay-view-model .ytBadgeShapeText'),
              el.querySelector('yt-thumbnail-badge-view-model .ytBadgeShapeText'),
              el.querySelector('badge-shape .ytBadgeShapeText'),
              el.querySelector('badge-shape.yt-badge-shape div.yt-badge-shape__text'),
              el.querySelector('div.yt-badge-shape__text'),
              el.querySelector('yt-thumbnail-overlay-time-status-view-model span'),
              el.querySelector('ytd-thumbnail-overlay-time-status-renderer span')
            ].filter(Boolean);
            const durationText = durationCandidates
              .map(el => el.textContent.trim())
              .find(text => /^(?:\d+:)?\d{1,2}:\d{2}$/.test(text)) || null;

            const thumbnail = el.querySelector('img.yt-core-image--loaded') || el.querySelector('yt-image img');
            const videoUrl = titleEl ? (titleEl.href && titleEl.href.startsWith('http') ? titleEl.href : null) : null;
            const channelUrl = channelAnchors.length ? channelAnchors[0].href : null;

            return {
              title: titleEl?.textContent?.trim() || null,
              url: videoUrl,
              videoId: (videoUrl && new URL(videoUrl).searchParams.get('v')) || dataBits.videoIdFromData || null,
              channelName: channelAnchors[0]?.textContent?.trim() || dataBits.channelNameFromData || null,
              channelLinks: channelAnchors,
              channelUrl: channelUrl,
              channelId: dataBits.channelId || (channelUrl ? (channelUrl.match(/@([\w-]+)/)?.[1] || channelUrl.match(/channel\/([\w-]+)/)?.[1]) : null),
              viewsText: viewsText,
              publishText: publishText,
              duration: durationText,
              thumbnail: thumbnail ? thumbnail.src : null,
              dataChannelId: dataBits.channelId,
              dataChannelName: dataBits.channelNameFromData,
              dataChannelUrl: dataBits.channelUrlFromData,
              dataVideoId: dataBits.videoIdFromData,
            };
          });
        }
        """


def run(debug: bool = False, scrolls: int = 6, title_filter: str | None = None) -> None:
    scraper = DebugVideoScraper(debug=debug, scrolls=scrolls, title_filter=title_filter)
    scraper.run()
