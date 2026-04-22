from .base_scraper import BaseScraper
from .db_schema import YouTubeDB
from datetime import UTC, datetime, timedelta
import re
import json
from urllib import request

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, TaskID

class VideoScraper(BaseScraper):
    def __init__(self, debug=False):
        super().__init__(debug)
        self.db = YouTubeDB()
        self.videos = []
        self.console = Console()

    def resolve_channel_using_oembed(self, video_id):
        """Fallback: hit YouTube oEmbed to recover channel info when the feed omits it (e.g., collab videos)."""
        url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        try:
            with request.urlopen(url, timeout=10) as resp:
                data = json.load(resp)
        except Exception:
            return None, None, None

        author_url = data.get("author_url")
        author_name = data.get("author_name")
        channel_id = None
        if author_url:
            handle_match = re.search(r'/@([A-Za-z0-9_-]+)', author_url)
            id_match = re.search(r'/channel/([A-Za-z0-9_-]+)', author_url)
            if id_match:
                channel_id = id_match.group(1)
            elif handle_match:
                channel_id = handle_match.group(1)
        return channel_id, author_url, author_name

    def parse_view_count(self, view_count_text):
        """Convert view count text like '3.2K views', '15K views', '3M views' into numbers."""
        if not view_count_text:
            return 0
        
        # Remove 'views' and any commas, then strip whitespace
        count = view_count_text.lower().replace('views', '').replace(',', '').strip()
        
        try:
            if 'k' in count:
                # Handle thousands (e.g., '3.2K', '15K')
                number = float(count.replace('k', '')) * 1000
            elif 'm' in count:
                # Handle millions (e.g., '3M', '3.4M')
                number = float(count.replace('m', '')) * 1000000
            else:
                # Handle regular numbers (e.g., '492')
                number = float(count)
            return int(number)
        except (ValueError, TypeError):
            print(f"Could not parse view count: {view_count_text}")
            return 0

    def parse_date(self, date_text):
        """Parse date from relative text like '1 month ago'"""
        if not date_text:
            raise ValueError("No date text provided")
        
        # Clean up the text
        text = date_text.lower().replace('streamed', '').strip()
        
        # Handle "just now", "moments ago", etc.
        if text in ['just now', 'moments ago']:
            return datetime.now(UTC).isoformat()
            
        # Handle "X minutes ago"
        if 'minute' in text:
            match = re.search(r'(\d+)\s*minute', text)
            if match:
                minutes = int(match.group(1))
                return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
        
        # Extract number and unit from relative date
        match = re.match(r'^(\d+)\s+(hour|day|week|month|year)s?\s+ago$', text)
        if not match:
            raise ValueError(f"Could not parse relative date format: '{date_text}'")
        
        number, unit = match.groups()
        number = int(number)
        
        now = datetime.now(UTC)
        
        if unit == 'hour':
            return (now - timedelta(hours=number)).isoformat()
        elif unit == 'day':
            return (now - timedelta(days=number)).isoformat()
        elif unit == 'week':
            return (now - timedelta(days=number * 7)).isoformat()
        elif unit == 'month':
            if number == 1:
                # Keep videos that show as "1 month ago"
                return (now - timedelta(days=29)).isoformat()
            else:
                # Multiple months old - definitely too old
                raise ValueError(f"Video too old: {number} months ago")
        elif unit == 'year':
            raise ValueError(f"Video too old: {number} years ago")
        else:
            raise ValueError(f"Unknown time unit in '{date_text}'")

    def parse_duration_seconds(self, duration_text):
        """Convert duration text like '12:34' or '1:02:10' into seconds."""
        if not duration_text:
            return None

        cleaned = duration_text.strip()
        if not cleaned:
            return None

        parts = cleaned.split(':')
        try:
            if len(parts) == 2:
                minutes, seconds = (int(part) for part in parts)
                return (minutes * 60) + seconds
            if len(parts) == 3:
                hours, minutes, seconds = (int(part) for part in parts)
                return (hours * 3600) + (minutes * 60) + seconds
        except ValueError:
            return None

        return None

    def scrape(self, days=30):
        """Fetch recent videos from subscriptions"""
        print("\nScanning YouTube subscriptions feed...")
        self.page.goto('https://www.youtube.com/feed/subscriptions')
        self.wait_for_page_load()
        
        # Calculate cutoff date - anything older than 30 days should be trimmed
        cutoff_date = datetime.now(UTC) - timedelta(days=30)
        
        # Clean up old videos - anything that's now too old to show up in feed
        cursor = self.db.db.cursor()
        cursor.execute('DELETE FROM videos WHERE published_date < ?', (cutoff_date.isoformat(),))
        trimmed_count = cursor.rowcount
        self.db.db.commit()

        scrape_run_id = self.db.start_scrape_run("videos")
        
        processed_video_ids = set()
        old_videos_count = 0
        max_old_videos = 3  # Stop after finding this many old videos
        total_new = 0
        total_updated = 0
        missing_channel_video_ids: set[str] = set()
        missing_channel_reasons = {
            "untracked_channel": 0,
            "no_channel_metadata": 0,
        }
        missing_channel_log = []
        stop_reason = ""

        max_scrolls = 20

        progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold cyan]Scroll[/]"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=self.console,
            expand=True,
        )
        scroll_task: TaskID = progress.add_task("scrolling", total=max_scrolls)

        def render_status(scroll_num, phase, notes=""):
            stats = Table.grid(expand=True)
            stats.add_column(justify="left")
            stats.add_row(f"[white]Phase:[/] {phase}")
            stats.add_row(f"[cyan]Processed:[/] {len(processed_video_ids)}")
            stats.add_row(f"[green]New:[/] {total_new}   [yellow]Updated:[/] {total_updated}")
            stats.add_row(f"[red]Missing channels:[/] {len(missing_channel_video_ids)}")
            if notes:
                stats.add_row(f"[bright_magenta]Note:[/] {notes}")

            warn_panel = Panel(
                "\n".join(missing_channel_log[-4:]) or "No warnings",
                title="Recent warnings",
                border_style="red" if missing_channel_log else "dim",
                height=6,
            )

            body = Group(
                progress,
                Panel(stats, border_style="bright_blue", title=f"Scroll {scroll_num}/{max_scrolls}"),
                warn_panel,
            )
            return Panel(body, title="YouTube Subscriptions", border_style="bright_magenta")
        
        # Try different selectors for video elements
        selectors = [
            'ytd-rich-item-renderer:not([is-slim-media])',
            'ytd-rich-grid-media',
            'ytd-grid-video-renderer',
            '#contents > ytd-rich-item-renderer'
        ]
        
        last_height = 0
        no_new_content_count = 0
        max_no_new_content = 3
        
        with Live(render_status(0, "Starting up"), refresh_per_second=8, console=self.console, transient=False) as live:
            for i in range(max_scrolls):  # Max scrolls
                progress.update(scroll_task, completed=i)
                live.update(render_status(i + 1, "Loading feed"))
                
                try:
                    # Extract all video information in one JavaScript call
                    videos_info = self.page.evaluate(r"""() => {
                    const selectors = [
                        'ytd-rich-item-renderer:not([is-slim-media])',
                        'ytd-rich-grid-media',
                        'ytd-grid-video-renderer',
                        '#contents > ytd-rich-item-renderer'
                    ];
                    let elements = [];

                    // Find first selector that returns elements
                    for (const selector of selectors) {
                        elements = Array.from(document.querySelectorAll(selector));
                        if (elements.length > 0) break;
                    }

                    // Extract info from each element
                    return elements.map(element => {
                        // Title - updated selector for new YouTube structure
                        const titleEl = element.querySelector('h3 a') ||
                                      element.querySelector('a#video-title-link') ||
                                      element.querySelector('#video-title');

                        // Channel - updated to find channel link in new structure
                        const channelEl = element.querySelector('a.yt-core-attributed-string__link') ||
                                        element.querySelector('[href*="/@"]') ||
                                        element.querySelector('[href*="/channel/"]');

                        // Filter out video links from channel links
                        const isChannelLink = channelEl && !channelEl.href.includes('/watch?v=');
                        const finalChannelEl = isChannelLink ? channelEl : null;

                        // Metadata - look for spans in the metadata area
                        const metadataSpans = element.querySelectorAll('yt-lockup-metadata-view-model span');
                        const channelTextFromMetadata = element.querySelector('yt-content-metadata-view-model span')?.textContent?.trim() || null;

                        // Thumbnail
                        const thumbnail = element.querySelector('img.yt-core-image--loaded') ||
                                        element.querySelector('yt-image img');

                        // Duration - updated selector for new YouTube structure
                        const durationCandidates = [
                            element.querySelector('yt-thumbnail-bottom-overlay-view-model .ytBadgeShapeText'),
                            element.querySelector('yt-thumbnail-badge-view-model .ytBadgeShapeText'),
                            element.querySelector('badge-shape .ytBadgeShapeText'),
                            element.querySelector('badge-shape.yt-badge-shape div.yt-badge-shape__text'),
                            element.querySelector('div.yt-badge-shape__text'),
                            element.querySelector('yt-thumbnail-overlay-time-status-view-model span'),
                            element.querySelector('ytd-thumbnail-overlay-time-status-renderer span')
                        ].filter(Boolean);
                        const durationText = durationCandidates
                            .map(el => el.textContent.trim())
                            .find(text => /^(?:\d+:)?\d{1,2}:\d{2}$/.test(text)) || null;

                        // Ensure URLs are absolute
                        const absolutize = (href) => {
                            if (!href) return null;
                            if (href.startsWith('http')) return href;
                            return new URL(href, 'https://www.youtube.com').href;
                        };
                        const videoUrl = titleEl ? absolutize(titleEl.getAttribute('href') || titleEl.href) : null;
                        const channelUrl = finalChannelEl ? absolutize(finalChannelEl.getAttribute('href') || finalChannelEl.href) : null;

                        // Parse metadata for views and publish date
                        let publishDate = null;
                        let views = 0;

                        metadataSpans.forEach(span => {
                            const text = span.textContent.trim();
                            if (text.includes('views')) {
                                views = text;
                            } else if (text.includes('ago') || text.includes('hour') || text.includes('day') ||
                                     text.includes('week') || text.includes('month') || text.includes('year')) {
                                publishDate = text;
                            }
                        });

                        // Get channel ID from URL (prefer handle over channel ID)
                        let channelId = null;
                        if (channelUrl) {
                            // First try to get handle
                            const handleMatch = channelUrl.match(/@([\w-]+)/);
                            if (handleMatch) {
                                channelId = handleMatch[1];
                            } else {
                                // Fallback to channel ID if no handle
                                const channelMatch = channelUrl.match(/channel\/([\w-]+)/);
                                if (channelMatch) {
                                    channelId = channelMatch[1];
                                }
                            }
                        }

                        return {
                            title: titleEl ? titleEl.textContent.trim() : null,
                            url: videoUrl,
                            channelName: finalChannelEl ? finalChannelEl.textContent.trim() : null,
                            channelUrl: channelUrl,
                            channelId: channelId,
                            channelText: channelTextFromMetadata,
                            views: views,
                            publishDate: publishDate,
                            thumbnailUrl: thumbnail ? thumbnail.src : null,
                            duration: durationText
                        };
                    });
                }""")
                
                    if not videos_info:
                        no_new_content_count += 1
                        if no_new_content_count >= max_no_new_content:
                            stop_reason = "No new content found"
                            break
                        if i < 5:
                            self.wait_for_page_load(2)
                            continue
                        else:
                            stop_reason = "No content returned from page"
                            break
                    
                    batch_size = 10
                    new_in_this_scroll = 0
                    updated_in_this_scroll = 0
                    
                    for j in range(0, len(videos_info), batch_size):
                        batch = videos_info[j:j+batch_size]
                        
                        for info in batch:
                            try:
                                if not info['title'] or not info['url']:
                                    continue
                                
                                video_info = {
                                    'title': info['title'],
                                    'url': info['url'],
                                    'channel_name': info['channelName'] or info.get('channelText'),
                                    'channel_url': info['channelUrl'],
                                    'channel_id': info['channelId'],
                                    'views': self.parse_view_count(info['views']),
                                    'thumbnail': info['thumbnailUrl'],
                                    'duration': info.get('duration')
                                }
                                
                                try:
                                    video_info['publish_date'] = self.parse_date(info['publishDate']) if info['publishDate'] else None
                                except ValueError as e:
                                    if "too old" in str(e):
                                        old_videos_count += 1
                                        if old_videos_count >= max_old_videos:
                                            stop_reason = "Reached older content in feed"
                                            break
                                        continue
                                    else:
                                        continue
                                
                                if not video_info['publish_date']:
                                    continue
                                
                                video_id_match = re.search(r'v=([\w-]+)', video_info['url'])
                                if not video_id_match:
                                    continue
                                
                                video_id = video_id_match.group(1)
                                if video_id in processed_video_ids:
                                    continue

                                parse_confidence = 1.0
                                resolution_method = "feed_dom"
                                resolved_channel_id, db_resolution = self.db.resolve_channel_id(
                                    video_info.get('channel_id'),
                                    video_info.get('channel_url'),
                                    video_info.get('channel_name'),
                                )

                                if not resolved_channel_id:
                                    resolved_id, resolved_url, resolved_name = self.resolve_channel_using_oembed(video_id)
                                    if resolved_id:
                                        video_info['channel_id'] = resolved_id
                                    if resolved_url and not video_info.get('channel_url'):
                                        video_info['channel_url'] = resolved_url
                                    if resolved_name and not video_info.get('channel_name'):
                                        video_info['channel_name'] = resolved_name
                                    if resolved_id or resolved_url or resolved_name:
                                        parse_confidence = min(parse_confidence, 0.85)
                                        resolution_method = "oembed"
                                        resolved_channel_id, db_resolution = self.db.resolve_channel_id(
                                            video_info.get('channel_id'),
                                            video_info.get('channel_url'),
                                            video_info.get('channel_name'),
                                        )

                                if not resolved_channel_id:
                                    processed_video_ids.add(video_id)
                                    if video_id not in missing_channel_video_ids:
                                        missing_channel_video_ids.add(video_id)
                                        has_channel_metadata = bool(
                                            video_info.get('channel_id')
                                            or video_info.get('channel_url')
                                            or video_info.get('channel_name')
                                        )
                                        reason = "untracked_channel" if has_channel_metadata else "no_channel_metadata"
                                        missing_channel_reasons[reason] += 1
                                        reason_label = "untracked channel" if has_channel_metadata else "missing channel metadata"
                                        channel_hint = (
                                            video_info.get('channel_name')
                                            or video_info.get('channel_id')
                                            or video_info.get('channel_url')
                                            or "unknown"
                                        )
                                        missing_channel_log.append(
                                            f"{video_info['title']} ({video_id}) - {reason_label}: {channel_hint}"
                                        )
                                    continue

                                cursor = self.db.db.cursor()
                                resolution_method = db_resolution if resolution_method == "feed_dom" else f"{resolution_method}+{db_resolution}"
                                if db_resolution in {"handle_url", "direct"}:
                                    parse_confidence = min(parse_confidence, 0.95)

                                processed_video_ids.add(video_id)
                                
                                if video_info['publish_date']:
                                    try:
                                        publish_date = datetime.fromisoformat(video_info['publish_date'])
                                        if publish_date < cutoff_date:
                                            old_videos_count += 1
                                            if old_videos_count >= max_old_videos:
                                                stop_reason = "Reached content older than 30 days"
                                                break
                                            continue
                                    except ValueError:
                                        continue

                                cursor.execute('SELECT id FROM videos WHERE id = ?', (video_id,))
                                existing_video = cursor.fetchone()

                                duration_text = video_info.get('duration')
                                duration_seconds = self.parse_duration_seconds(duration_text)
                                self.db.upsert_video(
                                    video_id=video_id,
                                    channel_id=resolved_channel_id,
                                    title=video_info['title'],
                                    url=video_info['url'],
                                    thumbnail=video_info['thumbnail'],
                                    views=video_info['views'],
                                    published_date=video_info['publish_date'],
                                    duration_text=duration_text,
                                    duration_seconds=duration_seconds,
                                    parse_confidence=parse_confidence,
                                    channel_resolution_method=resolution_method,
                                )

                                age_hours = max((datetime.now(UTC) - publish_date).total_seconds() / 3600.0, 0.0)
                                self.db.record_video_observation(
                                    video_id=video_id,
                                    scrape_run_id=scrape_run_id,
                                    views=video_info['views'],
                                    age_hours=age_hours,
                                    parse_confidence=parse_confidence,
                                )

                                if existing_video:
                                    updated_in_this_scroll += 1
                                    total_updated += 1
                                else:
                                    new_in_this_scroll += 1
                                    total_new += 1
                                
                                old_videos_count = 0
                                
                                if (new_in_this_scroll + updated_in_this_scroll) % 5 == 0:
                                    live.update(render_status(i + 1, "Processing videos"))
                            
                            except Exception:
                                continue

                        if stop_reason:
                            break

                    if stop_reason:
                        live.update(render_status(i + 1, "Stopping", stop_reason))
                        break

                    if new_in_this_scroll == 0 and updated_in_this_scroll == 0:
                        no_new_content_count += 1
                        if no_new_content_count >= max_no_new_content:
                            stop_reason = "No new/updated videos after repeated scrolls"
                            break
                    else:
                        no_new_content_count = 0
                    
                    current_height = self.page.evaluate('document.documentElement.scrollHeight')
                    if current_height == last_height:
                        no_new_content_count += 1
                        if no_new_content_count >= max_no_new_content:
                            stop_reason = "Reached end of feed"
                            break
                    
                    last_height = current_height
                    live.update(render_status(i + 1, "Scrolling feed"))
                    self.scroll_page()
                    
                except Exception:
                    if i < 5:
                        self.wait_for_page_load(2)
                        continue
                    else:
                        stop_reason = "Error while scrolling - stopping early"
                        break

                live.update(render_status(i + 1, "Processing latest videos"))

        self.db.finish_scrape_run(scrape_run_id)

        reason_text = stop_reason or "Completed planned scrolls"
        self.console.print(f"\n[bold green]󰗣  Scan complete ({reason_text}).[/] {total_new} new videos added, {total_updated} videos updated, {trimmed_count} videos trimmed.")
        skipped_missing_channels = len(missing_channel_video_ids)
        if skipped_missing_channels:
            self.console.print(
                f"[bold yellow]⚠️  Skipped {skipped_missing_channels} unique videos whose channels are not in the database.[/]"
            )
            self.console.print(
                "[yellow]Reasons:[/] "
                f"{missing_channel_reasons['untracked_channel']} untracked channel, "
                f"{missing_channel_reasons['no_channel_metadata']} missing channel metadata"
            )
            if missing_channel_log:
                self.console.print("Examples:")
                for entry in missing_channel_log[:5]:
                    self.console.print(f"  - {entry}")
                if len(missing_channel_log) > 5:
                    self.console.print(f"  ... and {len(missing_channel_log) - 5} more")
            self.console.print("Run `uv run ytsubs scrape-channels` to refresh channel records, then re-run video scrape.")

    def extract_video_info(self, page):
        """Extract video information from the page."""
        try:
            # Get all video elements
            script = """
            Array.from(document.querySelectorAll('ytd-grid-video-renderer')).map(video => {
                const viewCountText = video.querySelector('#metadata-line span:first-child')?.textContent || '';
                const timeText = video.querySelector('#metadata-line span:last-child')?.textContent || '';
                const titleElement = video.querySelector('#video-title');
                
                return {
                    id: video.querySelector('a#thumbnail')?.href?.split('v=')[1]?.split('&')[0] || '',
                    title: titleElement?.textContent?.trim() || '',
                    url: titleElement?.href || '',
                    thumbnail: video.querySelector('img#img')?.src || '',
                    views: viewCountText,
                    published_date: timeText,
                    channel_id: video.querySelector('ytd-channel-name a')?.href?.split('/channel/')[1]?.split('?')[0] || 
                               video.querySelector('ytd-channel-name a')?.href?.split('@')[1]?.split('/')[0] || ''
                };
            });
            """
            
            videos = page.evaluate(script)
            
            # Process each video
            processed_videos = []
            for video in videos:
                if not video['id'] or not video['title']:
                    continue
                    
                # Parse view count
                video['views'] = self.parse_view_count(video['views'])
                
                # Parse date
                video['published_date'] = self.parse_date(video['published_date'])
                
                processed_videos.append(video)
                
            return processed_videos
        except Exception as e:
            print(f"Error extracting video info: {str(e)}")
            return []

def run(debug: bool = False, generate_feed_after: bool = True) -> None:
    scraper = VideoScraper(debug=debug)
    scraper.run()  # Use run() instead of scrape() to ensure proper setup

    if not generate_feed_after:
        return

    # Check if we have channel data and generate feed if we do
    db = scraper.db
    cursor = db.db.cursor()
    cursor.execute('SELECT COUNT(*) FROM channels WHERE subscriber_count > 0')
    channel_count = cursor.fetchone()[0]

    if channel_count > 0:
        print("\nChannel data found - generating feed...")
        from . import generate_feed

        generate_feed.run()
    else:
        print("\nNo channel data found. Run `ytsubs scrape-channels` to collect channel data.")
