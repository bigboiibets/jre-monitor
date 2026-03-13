"""
keyword_engine.py — Stateful keyword scanner with per-episode dedup.

Keywords are loaded from keywords.json on every segment so you can
edit the file while the bot is running without restarting.

Dedup logic: once a keyword triggers an alert, suppress re-alerts for
DEDUP_WINDOW_MINUTES. After the window expires, a new alert fires if
the keyword appears again.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class KeywordHit:
    term: str
    found_at_seconds: float
    first_timestamp_str: str   # "HH:MM:SS" — first occurrence this episode
    mention_count: int          # total mentions so far this episode
    context_snippet: str        # ~120 chars surrounding the match
    source: str = ""            # filled in by the caller (e.g. "LIVE TRANSCRIPTION")


def format_timestamp(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


class KeywordEngine:
    def __init__(self, keywords_file: str, dedup_window_minutes: int = 10):
        self._keywords_file = Path(keywords_file)
        self._dedup_window = timedelta(minutes=dedup_window_minutes)

        # Per-episode state — reset via reset() at the start of each episode
        self._last_alert_time: dict[str, datetime] = {}
        self._mention_counts: dict[str, int] = {}
        self._first_timestamps: dict[str, float] = {}

    def reset(self):
        """Call at the start of each new episode to clear all accumulated state."""
        self._last_alert_time.clear()
        self._mention_counts.clear()
        self._first_timestamps.clear()
        logger.debug("KeywordEngine state reset for new episode")

    def _load_keywords(self) -> list[str]:
        """Read keywords.json. Supports both plain list and object format."""
        try:
            data = json.loads(self._keywords_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                items = data
            else:
                items = data.get("keywords", [])

            result = []
            for item in items:
                if isinstance(item, str):
                    result.append(item)
                elif isinstance(item, dict) and item.get("enabled", True):
                    result.append(item["term"])
            return result
        except FileNotFoundError:
            logger.error(f"keywords.json not found at: {self._keywords_file}")
            return []
        except Exception as e:
            logger.error(f"Failed to load keywords: {e}")
            return []

    def process_segment(self, text: str, start_seconds: float) -> list[KeywordHit]:
        """
        Scan a transcript segment for all keywords.

        Returns only hits that should trigger a new Telegram alert (i.e. not
        suppressed by the dedup window). Updates internal mention counts.
        """
        keywords = self._load_keywords()
        if not keywords:
            return []

        hits: list[KeywordHit] = []
        now = datetime.now(timezone.utc)
        text_lower = text.lower()

        for term in keywords:
            term_lower = term.lower()
            count = text_lower.count(term_lower)
            if count == 0:
                continue

            # Accumulate total mention count for this episode
            self._mention_counts[term] = self._mention_counts.get(term, 0) + count

            # Record the first occurrence timestamp
            if term not in self._first_timestamps:
                self._first_timestamps[term] = start_seconds

            # Dedup: skip if we alerted for this keyword recently
            last = self._last_alert_time.get(term)
            if last and (now - last) < self._dedup_window:
                continue

            # Build a context snippet (~120 chars around the first occurrence)
            idx = text_lower.find(term_lower)
            snip_start = max(0, idx - 60)
            snip_end = min(len(text), idx + len(term) + 60)
            prefix = "..." if snip_start > 0 else ""
            suffix = "..." if snip_end < len(text) else ""
            snippet = prefix + text[snip_start:snip_end].strip() + suffix

            self._last_alert_time[term] = now
            hits.append(KeywordHit(
                term=term,
                found_at_seconds=start_seconds,
                first_timestamp_str=format_timestamp(self._first_timestamps[term]),
                mention_count=self._mention_counts[term],
                context_snippet=snippet,
            ))

        return hits
