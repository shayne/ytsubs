import json
import re

from .base_scraper import BaseScraper
from .db_schema import YouTubeDB

class ChannelStatsScraper(BaseScraper):
    def __init__(self, debug=False):
        super().__init__(debug)
        self.db = YouTubeDB()

    def parse_subscriber_count(self, count_text):
        """Parse subscriber count from text like '1.2M subscribers', '500K subscribers', etc."""
        if not count_text:
            print(f"No subscriber count text provided")
            return None
            
        try:
            print(f"Parsing subscriber count from: '{count_text}'")
            # Remove 'subscribers' and any commas, then strip whitespace
            count = count_text.lower().replace('subscribers', '').replace(',', '').strip()
            
            # Handle special cases
            if count.startswith('@'):  # This is a handle, not a count
                print(f"Got channel handle instead of subscriber count: {count}")
                return None
                
            if count == 'no':  # "No subscribers"
                print(f"Channel has no subscribers")
                return 0
            
            # Remove any leading text like "Verified" or channel name
            count = re.sub(r'^.*?(\d)', r'\1', count)
            
            if 'k' in count or 'thousand' in count:
                # Handle thousands (e.g., '500K', '290 thousand')
                normalized = count.replace('thousand', '').replace('k', '').strip()
                number = float(normalized) * 1000
            elif 'm' in count or 'million' in count:
                # Handle millions (e.g., '1.2M', '7.14 million')
                normalized = count.replace('million', '').replace('m', '').strip()
                number = float(normalized) * 1000000
            elif 'b' in count or 'billion' in count:
                # Handle billions (e.g., '1.2B', '1.2 billion')
                normalized = count.replace('billion', '').replace('b', '').strip()
                number = float(normalized) * 1000000000
            else:
                # Handle regular numbers (e.g., '500')
                number = float(count)
            
            result = int(number)
            print(f"Successfully parsed subscriber count '{count_text}' -> {result:,}")
            return result
            
        except (ValueError, TypeError) as e:
            print(f"Could not parse subscriber count '{count_text}': {str(e)}")
            return None

    def extract_channel_stats(self):
        """Extract all channel stats from the channels feed page"""
        try:
            print("\nGoing to channels feed page...")
            response = self.page.goto('https://www.youtube.com/feed/channels')
            self.page.wait_for_load_state('networkidle')
            
            # Get the full page source
            page_source = self.page.content()
            
            # Find all channel renderer JSON objects in the source
            channel_data = []
            
            # Find all instances of channel renderer objects
            pattern = r'\{"channelRenderer":.+?(?=,\{"channelRenderer"|$)'
            matches = re.finditer(pattern, page_source, re.DOTALL)
            
            for match in matches:
                try:
                    # Add closing brace if it was cut off by the regex
                    json_str = match.group(0)
                    if not json_str.endswith('}'):
                        json_str += '}'
                    
                    # Parse the JSON object
                    data = json.loads(json_str)
                    if data and 'channelRenderer' in data:
                        channel_data.append(data)
                except json.JSONDecodeError as e:
                    print(f"Error parsing channel JSON at position {match.start()}: {str(e)[:100]}")
                    continue
            
            print(f"\nFound {len(channel_data)} channels")
            
            channel_info = {}
            for data in channel_data:
                try:
                    channel = data['channelRenderer']
                    channel_id = channel.get('channelId')
                    
                    if not channel_id:
                        continue
                    
                    # YouTube currently puts subscriber count in videoCountText and handle in subscriberCountText.
                    subscriber_text = None
                    video_count_text = channel.get('videoCountText', {})
                    if isinstance(video_count_text, dict):
                        subscriber_text = (
                            video_count_text.get('simpleText')
                            or video_count_text.get('accessibility', {}).get('accessibilityData', {}).get('label', '')
                        )
                    if not subscriber_text:
                        subscriber_count_text = channel.get('subscriberCountText', {})
                        if isinstance(subscriber_count_text, dict):
                            subscriber_text = (
                                subscriber_count_text.get('simpleText')
                                or subscriber_count_text.get('accessibility', {}).get('accessibilityData', {}).get('label', '')
                            )

                    # Get channel handle from canonical URL or nav URL.
                    handle = None
                    custom_url = channel.get('navigationEndpoint', {}).get('browseEndpoint', {}).get('canonicalBaseUrl', '')
                    if isinstance(custom_url, str) and custom_url.startswith('@'):
                        handle = custom_url[1:]
                    if not handle:
                        nav_url = channel.get('navigationEndpoint', {}).get('commandMetadata', {}).get('webCommandMetadata', {}).get('url', '')
                        if isinstance(nav_url, str) and '/@' in nav_url:
                            handle = nav_url.split('/@', 1)[1].split('/')[0]
                    
                    # Check for verification badge
                    is_verified = False
                    owner_badges = channel.get('ownerBadges', [])
                    for badge in owner_badges:
                        badge_renderer = badge.get('metadataBadgeRenderer', {})
                        if badge_renderer.get('style') == 'BADGE_STYLE_TYPE_VERIFIED':
                            is_verified = True
                            break
                    
                    # Extract all channel information (canonical key: YouTube channel ID)
                    channel_info[channel_id] = {
                        'id': channel_id,
                        'name': channel.get('title', {}).get('simpleText', ''),
                        'url': 'https://youtube.com' + channel.get('navigationEndpoint', {}).get('commandMetadata', {}).get('webCommandMetadata', {}).get('url', ''),
                        'description': channel.get('descriptionSnippet', {}).get('runs', [{}])[0].get('text', ''),
                        'subscriber_count': self.parse_subscriber_count(subscriber_text),
                        'thumbnail_url': None,
                        'is_verified': is_verified,
                        'handle': handle,
                        'youtube_id': channel_id  # Store original YouTube ID as a reference
                    }
                    
                    # Get the highest resolution thumbnail
                    thumbnails = channel.get('thumbnail', {}).get('thumbnails', [])
                    if thumbnails:
                        channel_info[channel_id]['thumbnail_url'] = thumbnails[-1].get('url', '')
                        if channel_info[channel_id]['thumbnail_url'].startswith('//'):
                            channel_info[channel_id]['thumbnail_url'] = 'https:' + channel_info[channel_id]['thumbnail_url']

                    sub_count = channel_info[channel_id]['subscriber_count']
                    sub_display = f"{sub_count:,}" if isinstance(sub_count, int) else "unknown"
                    handle_display = f"@{channel_info[channel_id]['handle']}" if channel_info[channel_id]['handle'] else channel_id
                    print(f"Found channel {channel_info[channel_id]['name']} ({handle_display}): {sub_display} subscribers {'✓' if is_verified else ''}")
                    
                except Exception as e:
                    print(f"Error processing channel: {e}")
                    continue
            
            return channel_info
            
        except Exception as e:
            print(f"Error extracting channel stats: {e}")
            return {}

    def get_channel_average_views(self, channel_url):
        """Calculate average views from the last 30 videos by visiting the channel page, excluding top and bottom 3 performing videos"""
        try:
            print(f"\nGetting average views from {channel_url}...")
            
            # Use commit instead of domcontentloaded - faster and still reliable
            try:
                self.page.goto(channel_url + '/videos', timeout=5000, wait_until='commit')
            except Exception as e:
                print(f"Initial page load timed out, but continuing anyway: {e}")
            
            # Wait for any video to appear
            try:
                self.page.wait_for_selector('ytd-rich-grid-media, ytd-grid-video-renderer', timeout=3000)
            except Exception as e:
                print(f"Warning: Video grid not found: {e}")
                return 0  # Return early if no videos found
            
            # Quick double-scroll to load more videos
            try:
                self.page.evaluate('''() => {
                    window.scrollTo(0, document.documentElement.scrollHeight / 2);
                    setTimeout(() => window.scrollTo(0, document.documentElement.scrollHeight), 250);
                }''')
                self.page.wait_for_timeout(300)  # Brief wait for lazy loading
            except Exception as e:
                print(f"Warning: Scroll failed: {e}")
            
            # Extract video information using JavaScript - optimized to get all data in one pass
            videos_info = self.page.evaluate("""() => {
                const processViewCount = (text) => {
                    if (!text) return 0;
                    text = text.toLowerCase().replace('views', '').replace(',', '').trim();
                    try {
                        if (text.includes('k')) {
                            return parseFloat(text.replace('k', '')) * 1000;
                        } else if (text.includes('m')) {
                            return parseFloat(text.replace('m', '')) * 1000000;
                        } else {
                            return parseFloat(text);
                        }
                    } catch {
                        return 0;
                    }
                };

                const videos = Array.from(document.querySelectorAll('ytd-rich-grid-media, ytd-grid-video-renderer')).slice(0, 30);
                const views = videos.map(video => {
                    const metadataLine = video.querySelector('#metadata-line');
                    if (!metadataLine) return 0;
                    
                    const spans = Array.from(metadataLine.querySelectorAll('span'));
                    for (const span of spans) {
                        if (span.textContent.includes('views')) {
                            return processViewCount(span.textContent);
                        }
                    }
                    return 0;
                }).filter(views => views > 0);

                return views;
            }""")
            
            if not videos_info:
                print("No videos found on channel page")
                return 0
            
            # videos_info is now already an array of numbers, no need for parsing
            views = videos_info
            
            if len(views) >= 10:  # Only remove outliers if we have enough videos
                views.sort()
                
                # Calculate quartiles for better outlier detection
                q1_index = len(views) // 4
                q3_index = 3 * len(views) // 4
                q1 = views[q1_index]
                q3 = views[q3_index]
                iqr = q3 - q1
                
                # Remove extreme outliers (beyond 2.5 * IQR)
                lower_bound = max(0, q1 - 2.5 * iqr)
                upper_bound = q3 + 2.5 * iqr
                
                original_count = len(views)
                views = [v for v in views if lower_bound <= v <= upper_bound]
                
                if len(views) < original_count:
                    print(f"Removed {original_count - len(views)} outliers from average calculation")
                    print(f"View range after outlier removal: {min(views):,} - {max(views):,}")
            
            if views:
                # Use trimmed mean: remove top and bottom 10% for more robust average
                if len(views) >= 5:
                    trim_count = max(1, len(views) // 10)
                    trimmed_views = views[trim_count:-trim_count]
                    average = int(sum(trimmed_views) / len(trimmed_views))
                    print(f"Calculated trimmed average from {len(trimmed_views)} videos (excluded {trim_count} from each end): {average:,}")
                else:
                    average = int(sum(views) / len(views))
                    print(f"Calculated average views from {len(views)} videos: {average:,}")
                return average
            
            print("No valid view counts found")
            return 0
            
        except Exception as e:
            print(f"Error getting channel average views: {e}")
            return 0

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

    def update_channel_info(self, channel_id, info):
        """Update channel information in the database"""
        cursor = self.db.db.cursor()
        
        try:
            # Calculate 48h baseline proxy from recent uploads.
            baseline_48h = self.get_channel_average_views(info['url'])
            
            # First check if channel exists
            cursor.execute('SELECT id FROM channels WHERE id = ?', (channel_id,))
            channel_exists = cursor.fetchone() is not None
            
            if channel_exists:
                # Update only specific fields for existing channels.
                cursor.execute('''
                    UPDATE channels 
                    SET name = ?,
                        url = ?,
                        youtube_id = ?,
                        description = ?,
                        thumbnail_url = ?,
                        subscriber_count = ?,
                        is_verified = ?,
                        handle = ?,
                        baseline_48h = ?,
                        baseline_updated_at = CURRENT_TIMESTAMP,
                        last_updated = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (
                    info['name'],
                    info['url'],
                    info['youtube_id'],
                    info['description'],
                    info['thumbnail_url'],
                    info['subscriber_count'],
                    info['is_verified'],
                    info['handle'],
                    baseline_48h,
                    channel_id
                ))
                print(f"Updated channel {info['name']} with {info['subscriber_count']:,} subscribers (48h baseline: {baseline_48h:,})")
            else:
                # Insert new channel with all fields
                cursor.execute('''
                    INSERT INTO channels 
                    (
                        id,
                        youtube_id,
                        name,
                        url,
                        subscriber_count,
                        description,
                        thumbnail_url,
                        is_verified,
                        handle,
                        baseline_48h,
                        baseline_updated_at,
                        last_updated
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ''', (
                    channel_id,
                    info['youtube_id'],
                    info['name'],
                    info['url'],
                    info['subscriber_count'],
                    info['description'],
                    info['thumbnail_url'],
                    info['is_verified'],
                    info['handle'],
                    baseline_48h
                ))
                print(f"Inserted new channel {info['name']} with {info['subscriber_count']:,} subscribers (48h baseline: {baseline_48h:,})")
            
            self.db.db.commit()
            
        except Exception as e:
            print(f"Error updating channel {channel_id}: {e}")
            self.db.db.rollback()

    def scrape(self):
        """Scrape channel information from the subscriptions page"""
        print("\nFetching channel statistics...")
        
        # Get all channel stats from the feed page
        channel_info = self.extract_channel_stats()
        
        if not channel_info:
            print("No channels found on subscriptions page.")
            return
        
        # Update information for all channels found
        updated_count = 0
        total_channels = len(channel_info)
        
        for i, (channel_id, info) in enumerate(channel_info.items(), 1):
            try:
                print(f"\nProcessing channel {i}/{total_channels}: {info['name']}")
                subscriber_count = info.get('subscriber_count')
                if subscriber_count is not None:  # Only update if we have a valid subscriber count
                    self.update_channel_info(channel_id, info)
                    print(f"Updated info for {info['name']} (@{info['handle']}): {subscriber_count:,} subscribers")
                    updated_count += 1
                else:
                    print(f"Skipping update for {info['name']} - no valid subscriber count")
            except Exception as e:
                print(f"Error updating channel {channel_id}: {e}")
        
        print(f"\nFinished updating {updated_count} channel statistics!")

def run(debug: bool = False) -> None:
    scraper = ChannelStatsScraper(debug=debug)
    scraper.run()
