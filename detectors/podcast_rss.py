"""
detectors/podcast_rss.py — Polls the JRE podcast RSS feed (Megaphone-hosted).

This runs in parallel with YouTube RSS as a second detection path.
The podcast RSS may update at a different time than YouTube, so running
both maximises the chance of early detection.

Default feed: https://feeds.megaphone.fm/GLT1412515089
Configure via JRE_PODCAST_RSS_URL in .env (or leave blank to disable).
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiohttp
import feedparser

from detectors.youtube_rss import Episode

logger = logging.getLogger(__name__)


class PodcastRSSDetector:
    def __init__(self, rss_url: str):
        self.rss_url = rss_url

    async def check(self) -> list[Episode]:
        if not self.rss_url:
            return []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.rss_url,
                    timeout=aiohttp.ClientTimeout(total=20),
                    headers={"User-Agent": "Mozilla/5.0 jre-monitor/1.0"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"Podcast RSS HTTP {resp.status}")
                        return []
                    xml = await resp.text()

            loop = asyncio.get_running_loop()
            feed = await loop.run_in_executor(None, feedparser.parse, xml)

            episodes: list[Episode] = []
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

            for entry in feed.entries[:5]:  # only check the 5 most recent
                try:
                    published = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc
                    )
                except (AttributeError, TypeError):
                    continue

                if published < cutoff:
                    continue

                title: str = entry.get("title", "")
                episode_id: str = entry.get("id", entry.get("guid", ""))
                link: str = entry.get("link", "")

                if not episode_id:
                    continue

                episodes.append(Episode(
                    episode_id=f"podcast_{episode_id}",
                    title=title,
                    youtube_url=link,
                    source="podcast_rss",
                    published_at=published,
                ))

            return episodes

        except asyncio.TimeoutError:
            logger.warning("Podcast RSS check timed out")
            return []
        except Exception as e:
            logger.warning(f"Podcast RSS check failed: {e}")
            return []
