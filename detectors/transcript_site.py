"""
detectors/transcript_site.py — Polls transcript websites for the episode text.

This is the second arm of the race architecture. Transcript sites typically
publish transcripts 5–30 minutes after an episode goes live, which is slower
than live audio transcription but serves as a fallback and cross-check.

Current sites:
  - podscripts.co (primary)

Add new sites by implementing a method following the _try_podscripts() pattern
and adding it to _try_all_sites().
"""

import logging
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class TranscriptSiteDetector:
    def __init__(self):
        pass

    async def fetch(self, episode) -> list[dict]:
        """
        Try to fetch a transcript for the given episode from all known sites.

        Returns a list of segment dicts {"text": str, "start": float} if found,
        or an empty list if no transcript is available yet.
        """
        results = await self._try_all_sites(episode)
        return results

    async def _try_all_sites(self, episode) -> list[dict]:
        """Try each site in order, return first successful result."""
        for site_method in [self._try_podscripts]:
            try:
                segments = await site_method(episode)
                if segments:
                    logger.info(
                        f"Transcript found via {site_method.__name__} "
                        f"({len(segments)} segments)"
                    )
                    return segments
            except Exception as e:
                logger.debug(f"{site_method.__name__} failed: {e}")
        return []

    async def _try_podscripts(self, episode) -> list[dict]:
        """
        Try to find and scrape a transcript from podscripts.co.

        Strategy: fetch the JRE listing page, look for an episode link whose
        text matches the current episode title, then scrape that page.
        """
        listing_url = "https://podscripts.co/podcasts/the-joe-rogan-experience"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    listing_url,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"User-Agent": "Mozilla/5.0 jre-monitor/1.0"},
                ) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()

            soup = BeautifulSoup(html, "lxml")

            # Extract key words from episode title for fuzzy matching
            title_words = [
                w.lower() for w in episode.title.split()
                if len(w) > 3 and w.isalpha()
            ][:4]  # use first 4 meaningful words

            for link in soup.find_all("a", href=True):
                link_text = link.get_text(strip=True).lower()
                href = link.get("href", "")

                # Match if 2+ title words appear in the link text
                matches = sum(1 for w in title_words if w in link_text)
                if matches >= 2 and "/podcasts/" in href:
                    transcript_url = (
                        f"https://podscripts.co{href}"
                        if href.startswith("/")
                        else href
                    )
                    return await self._scrape_transcript_page(transcript_url)

        except Exception as e:
            logger.debug(f"podscripts listing fetch failed: {e}")

        return []

    async def _scrape_transcript_page(self, url: str) -> list[dict]:
        """Fetch a transcript page and extract text segments."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=20),
                    headers={"User-Agent": "Mozilla/5.0 jre-monitor/1.0"},
                ) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()

            soup = BeautifulSoup(html, "lxml")
            segments: list[dict] = []
            offset = 0.0

            # Try transcript-specific containers first
            containers = soup.find_all(
                ["p", "div", "span"],
                class_=lambda c: c and any(
                    word in str(c).lower()
                    for word in ["transcript", "utterance", "segment", "text"]
                ),
            )

            if not containers:
                # Fallback: all paragraphs with substantial text
                containers = [p for p in soup.find_all("p") if len(p.get_text()) > 50]

            for el in containers:
                text = el.get_text(separator=" ", strip=True)
                if len(text) > 30:
                    segments.append({"text": text, "start": offset})
                    offset += 30.0  # approximate 30s per segment

            return segments

        except Exception as e:
            logger.debug(f"Transcript page scrape failed for {url}: {e}")
            return []
