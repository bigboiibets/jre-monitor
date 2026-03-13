"""
storage.py — Async SQLite wrapper (aiosqlite).

All state lives here: seen episodes, keyword alerts, transcript segments,
latency events. Uses INSERT OR IGNORE for idempotency on restart.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def __aenter__(self):
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._create_tables()
        return self

    async def __aexit__(self, *args):
        if self._db:
            await self._db.close()

    async def _create_tables(self):
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS episodes (
                episode_id       TEXT PRIMARY KEY,
                title            TEXT NOT NULL,
                youtube_url      TEXT,
                detected_at      TEXT NOT NULL,
                detected_source  TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'detected'
            );

            CREATE TABLE IF NOT EXISTS keyword_alerts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id       TEXT NOT NULL,
                term             TEXT NOT NULL,
                first_mention_ts TEXT NOT NULL,
                mention_count    INTEGER NOT NULL,
                context_snippet  TEXT,
                source           TEXT NOT NULL,
                alerted_at       TEXT NOT NULL,
                telegram_success INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS transcript_segments (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id       TEXT NOT NULL,
                start_seconds    REAL NOT NULL,
                text             TEXT NOT NULL,
                source           TEXT NOT NULL,
                created_at       TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS latency_events (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id       TEXT,
                event_name       TEXT NOT NULL,
                event_time       TEXT NOT NULL,
                delta_ms         REAL,
                note             TEXT
            );
        """)
        await self._db.commit()

    async def add_episode(
        self, episode_id: str, title: str, youtube_url: str, source: str
    ) -> bool:
        """Returns True if this is a new episode, False if already seen."""
        cursor = await self._db.execute(
            """INSERT OR IGNORE INTO episodes
               (episode_id, title, youtube_url, detected_at, detected_source)
               VALUES (?, ?, ?, ?, ?)""",
            (episode_id, title, youtube_url,
             datetime.now(timezone.utc).isoformat(), source),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def episode_seen(self, episode_id: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM episodes WHERE episode_id = ?", (episode_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def get_recent_episode_titles(self) -> list[str]:
        """Return titles of episodes detected in the last 48 hours."""
        async with self._db.execute(
            """SELECT title FROM episodes
               WHERE detected_at > datetime('now', '-48 hours')"""
        ) as cur:
            rows = await cur.fetchall()
            return [r["title"] for r in rows]

    async def update_episode_status(self, episode_id: str, status: str):
        await self._db.execute(
            "UPDATE episodes SET status = ? WHERE episode_id = ?",
            (status, episode_id),
        )
        await self._db.commit()

    async def log_keyword_alert(
        self,
        episode_id: str,
        term: str,
        first_mention_ts: str,
        mention_count: int,
        snippet: str,
        source: str,
        telegram_ok: bool,
    ):
        await self._db.execute(
            """INSERT INTO keyword_alerts
               (episode_id, term, first_mention_ts, mention_count,
                context_snippet, source, alerted_at, telegram_success)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (episode_id, term, first_mention_ts, mention_count, snippet,
             source, datetime.now(timezone.utc).isoformat(), int(telegram_ok)),
        )
        await self._db.commit()

    async def log_segment(
        self, episode_id: str, start_seconds: float, text: str, source: str
    ):
        await self._db.execute(
            """INSERT INTO transcript_segments
               (episode_id, start_seconds, text, source)
               VALUES (?, ?, ?, ?)""",
            (episode_id, start_seconds, text, source),
        )
        await self._db.commit()

    async def log_latency(
        self,
        episode_id: str,
        event_name: str,
        delta_ms: Optional[float],
        note: str = "",
    ):
        await self._db.execute(
            """INSERT INTO latency_events
               (episode_id, event_name, event_time, delta_ms, note)
               VALUES (?, ?, ?, ?, ?)""",
            (episode_id, event_name, datetime.now(timezone.utc).isoformat(),
             delta_ms, note),
        )
        await self._db.commit()
