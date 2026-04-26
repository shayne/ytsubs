from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any
from urllib import parse, request


YOUTUBE_API_BASE_URL = "https://www.googleapis.com/youtube/v3"


class YouTubeAPIError(RuntimeError):
    """Raised when the YouTube Data API request cannot be completed."""


def get_youtube_api_key() -> str | None:
    key = os.environ.get("YOUTUBE_API_KEY")
    if key:
        return key.strip()
    return None


def parse_youtube_duration(duration: str | None) -> int | None:
    if not duration:
        return None

    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?T?"
        r"(?:(?P<hours>\d+)H)?"
        r"(?:(?P<minutes>\d+)M)?"
        r"(?:(?P<seconds>\d+)S)?",
        duration,
    )
    if not match:
        return None

    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return (days * 86400) + (hours * 3600) + (minutes * 60) + seconds


def format_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None

    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def youtube_timestamp_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(UTC).isoformat()


class YouTubeAPIClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, endpoint: str, params: dict[str, str | int]) -> dict[str, Any]:
        query = dict(params)
        query["key"] = self.api_key
        url = f"{YOUTUBE_API_BASE_URL}/{endpoint}?{parse.urlencode(query)}"

        try:
            with request.urlopen(url, timeout=30) as response:
                payload = json.load(response)
        except Exception as exc:
            raise YouTubeAPIError(f"YouTube API request failed for {endpoint}: {exc}") from exc

        if not isinstance(payload, dict):
            raise YouTubeAPIError(f"YouTube API returned a non-object payload for {endpoint}")
        if "error" in payload:
            raise YouTubeAPIError(f"YouTube API returned an error for {endpoint}: {payload['error']}")
        return payload

    def iter_subscription_channel_ids(self, source_channel_id: str) -> Iterator[str]:
        page_token: str | None = None
        while True:
            params: dict[str, str | int] = {
                "part": "snippet",
                "channelId": source_channel_id,
                "maxResults": 50,
            }
            if page_token:
                params["pageToken"] = page_token

            payload = self._get("subscriptions", params)
            for item in payload.get("items", []):
                resource_id = item.get("snippet", {}).get("resourceId", {})
                channel_id = resource_id.get("channelId")
                if isinstance(channel_id, str) and channel_id:
                    yield channel_id

            page_token = payload.get("nextPageToken")
            if not isinstance(page_token, str) or not page_token:
                break

    def list_channels(self, channel_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = [channel_id for channel_id in channel_ids if channel_id]
        channels: list[dict[str, Any]] = []
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            if not batch:
                continue

            payload = self._get(
                "channels",
                {
                    "part": "snippet,statistics",
                    "id": ",".join(batch),
                    "maxResults": 50,
                },
            )
            for item in payload.get("items", []):
                channel_id = item.get("id")
                snippet = item.get("snippet", {})
                statistics = item.get("statistics", {})
                if not isinstance(channel_id, str) or not channel_id:
                    continue

                custom_url = snippet.get("customUrl")
                handle = None
                if isinstance(custom_url, str) and custom_url.startswith("@"):
                    handle = custom_url[1:]

                thumbnails = snippet.get("thumbnails", {})
                thumbnail_url = None
                if isinstance(thumbnails, dict):
                    for key in ("maxres", "high", "medium", "default"):
                        candidate = thumbnails.get(key, {}).get("url")
                        if candidate:
                            thumbnail_url = candidate
                            break

                subscriber_count = statistics.get("subscriberCount")
                channels.append(
                    {
                        "id": channel_id,
                        "title": snippet.get("title") or channel_id,
                        "url": f"https://www.youtube.com/channel/{channel_id}",
                        "handle": handle,
                        "description": snippet.get("description") or "",
                        "thumbnail_url": thumbnail_url,
                        "subscriber_count": int(subscriber_count) if subscriber_count else 0,
                        "is_verified": False,
                    }
                )
        return channels

    def list_recent_channel_video_ids(self, channel_id: str, published_after: str) -> list[str]:
        ids: list[str] = []
        page_token: str | None = None
        while True:
            params: dict[str, str | int] = {
                "part": "id",
                "channelId": channel_id,
                "type": "video",
                "order": "date",
                "publishedAfter": published_after,
                "maxResults": 50,
            }
            if page_token:
                params["pageToken"] = page_token

            payload = self._get("search", params)
            for item in payload.get("items", []):
                video_id = item.get("id", {}).get("videoId")
                if isinstance(video_id, str) and video_id:
                    ids.append(video_id)

            page_token = payload.get("nextPageToken")
            if not isinstance(page_token, str) or not page_token:
                break
        return ids

    def list_videos(self, video_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = [video_id for video_id in video_ids if video_id]
        videos: list[dict[str, Any]] = []
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            if not batch:
                continue

            payload = self._get(
                "videos",
                {
                    "part": "snippet,statistics,contentDetails",
                    "id": ",".join(batch),
                    "maxResults": 50,
                },
            )
            for item in payload.get("items", []):
                video_id = item.get("id")
                snippet = item.get("snippet", {})
                statistics = item.get("statistics", {})
                content_details = item.get("contentDetails", {})
                if not isinstance(video_id, str) or not video_id:
                    continue

                raw_duration = content_details.get("duration")
                duration_seconds = parse_youtube_duration(raw_duration)
                thumbnails = snippet.get("thumbnails", {})
                thumbnail = None
                if isinstance(thumbnails, dict):
                    for key in ("maxres", "standard", "high", "medium", "default"):
                        candidate = thumbnails.get(key, {}).get("url")
                        if candidate:
                            thumbnail = candidate
                            break

                view_count = statistics.get("viewCount")
                videos.append(
                    {
                        "id": video_id,
                        "channel_id": snippet.get("channelId"),
                        "title": snippet.get("title") or video_id,
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "thumbnail": thumbnail,
                        "views": int(view_count) if view_count else 0,
                        "published_date": youtube_timestamp_to_iso(snippet.get("publishedAt")),
                        "duration_text": format_duration(duration_seconds),
                        "duration_seconds": duration_seconds,
                    }
                )
        return videos
