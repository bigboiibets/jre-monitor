"""
detectors/youtube_rss.py — Polls the YouTube RSS feed for new JRE episodes.

No API key required. YouTube publishes a public Atom feed per channel that
typically updates within 60–120 seconds of a new upload going live.

Feed URL: https://www.youtube.com/feeds/videos.xml?channel_id=<ID>
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiohttp
import feedparser

logger = logging.getLogger(__name__)


@dataclass
class Episode:
    episode_id: str
    title: str
    youtube_url: str
    source: str
    published_at: Optional[datetime] = None


class YouTubeRSSDetector:
    def __init__(self, channel_id: str):
        if not channel_id:
            raise ValueError(
                "JRE_YOUTUBE_CHANNEL_ID is not set in .env. "
                "Find it at https://www.youtube.com/@joerogan → view page source → "
                "search for 'channelId'."
            )
        self.feed_url = (
            f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        )

    async def check(self) -> list[Episode]:
        """
        Fetch the channel RSS feed and return JRE episodes published
        within the last 24 hours that haven't been seen before.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.feed_url,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"User-Agent": "Mozilla/5.0 jre-monitor/1.0"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"YouTube RSS HTTP {resp.status}")
                        return []
                    xml = await resp.text()

            # feedparser is synchronous — run in executor to avoid blocking
            loop = asyncio.get_running_loop()
            feed = await loop.run_in_executor(None, feedparser.parse, xml)

            episodes: list[Episode] = []
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

            for entry in feed.entries:
                try:
                    published = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    )
                except (AttributeError, TypeError):
                    continue

                if published < cutoff:
                    continue

                title: str = entry.get("title", "")
                if not self._is_jre_episode(title):
                    continue

                # Extract video ID
                video_id = getattr(entry, "yt_videoid", None)
                if not video_id:
                    # Fallback: parse from entry.id ("yt:video:VIDEO_ID")
                    raw_id = entry.get("id", "")
                    video_id = raw_id.split(":")[-1] if ":" in raw_id else raw_id

                if not video_id:
                    continue

                url = f"https://www.youtube.com/watch?v={video_id}"
                episodes.append(Episode(
                    episode_id=video_id,
                    title=title,
                    youtube_url=url,
                    source="youtube_rss",
                    published_at=published,
                ))

            return episodes

        except asyncio.TimeoutError:
            logger.warning("YouTube RSS check timed out")
            return []
        except Exception as e:
            logger.warning(f"YouTube RSS check failed: {e}")
            return []

    @staticmethod
    def _is_jre_episode(title: str) -> bool:
        """
        Filter for full JRE episodes. Excludes clips, shorts, and other uploads.
        Adjust this if the channel posts non-episode content you want to ignore.
        """
        t = title.lower()
        return (
            "joe rogan experience" in t
            or t.startswith("#")
            or "jre" in t.split()  # e.g. "JRE #2000"
        )
