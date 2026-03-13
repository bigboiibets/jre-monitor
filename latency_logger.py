"""
latency_logger.py — Records named timing events and computes deltas.

Every stage of the pipeline calls lat.mark("event_name") so you can
measure exactly where time is being spent.

Usage:
    lat = LatencyLogger(episode_id)
    lat.mark("episode_detected")
    ...
    lat.mark("first_chunk_ready")
    ...
    lat.mark("keyword_hit_AI")
    lat.report()                     # prints full table to console
    await lat.flush_to_db(storage)   # persists to SQLite
"""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class LatencyLogger:
    def __init__(self, episode_id: str):
        self.episode_id = episode_id
        self._events: list[tuple[str, float, str]] = []  # (name, wall_time, note)
        self._start: Optional[float] = None
        self._first_marks: set[str] = set()

    def mark(self, event_name: str, note: str = "") -> float:
        """Record a timing event. Returns ms elapsed since first mark."""
        ts = time.time()
        if self._start is None:
            self._start = ts
        delta_ms = (ts - self._start) * 1000
        self._events.append((event_name, ts, note))
        logger.info(f"[LAT] {event_name}: +{delta_ms:.0f}ms  {note}")
        return delta_ms

    def mark_first(self, event_name: str, note: str = "") -> float:
        """Mark only on the first call — ignores subsequent calls with the same name."""
        if event_name not in self._first_marks:
            self._first_marks.add(event_name)
            return self.mark(event_name, note)
        return 0.0

    def report(self):
        """Print a full latency breakdown to the log."""
        if not self._events or self._start is None:
            return
        logger.info("=" * 55)
        logger.info(f"LATENCY REPORT  episode={self.episode_id}")
        logger.info("=" * 55)
        for name, ts, note in self._events:
            delta = (ts - self._start) * 1000
            note_str = f"  ({note})" if note else ""
            logger.info(f"  {name:<42s} +{delta:7.0f}ms{note_str}")
        logger.info("=" * 55)

    async def flush_to_db(self, storage):
        """Persist all events to the latency_events table."""
        for name, ts, note in self._events:
            delta = (ts - self._start) * 1000 if self._start else None
            await storage.log_latency(self.episode_id, name, delta, note)
