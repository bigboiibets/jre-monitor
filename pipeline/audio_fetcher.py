"""
pipeline/audio_fetcher.py — Extracts a direct audio stream URL via yt-dlp.

yt-dlp is called as a subprocess with --get-url to return the direct
CDN URL of the best available audio track. This URL is then passed
to ffmpeg for segmentation without downloading the full file first.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def get_audio_url(video_url: str) -> Optional[str]:
    """
    Use yt-dlp to extract the best audio stream URL for a YouTube video.

    Returns the CDN URL string, or None on failure.
    Does NOT download the audio — just resolves the URL.
    """
    cmd = [
        "yt-dlp",
        "--format", "bestaudio[ext=m4a]/bestaudio/best",
        "--get-url",
        "--quiet",
        "--no-warnings",
        "--no-playlist",
        video_url,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=45.0)
        url = stdout.decode().strip().split("\n")[0]
        if url:
            logger.info(f"Audio URL acquired (prefix: {url[:80]}...)")
            return url
        logger.error("yt-dlp returned empty URL")
        return None

    except asyncio.TimeoutError:
        logger.error("yt-dlp timed out after 45s")
        return None
    except FileNotFoundError:
        logger.error(
            "yt-dlp not found. Install it with: pip install yt-dlp"
        )
        return None
    except Exception as e:
        logger.error(f"yt-dlp failed: {e}")
        return None
