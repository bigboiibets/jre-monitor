"""
notifier.py — Telegram alert delivery (async, MarkdownV2).

Three alert types:
  1. Episode detected  — fires immediately on new episode
  2. Keyword hit       — fires per keyword, shows context + source
  3. Test alert        — verifies your Telegram setup
"""

import logging
import asyncio

import aiohttp

from keyword_engine import KeywordHit

logger = logging.getLogger(__name__)

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

# All characters that require escaping in Telegram MarkdownV2
_MD2_CHARS = r"\_*[]()~`>#+-=|{}.!"


def _esc(text: str) -> str:
    result = []
    for ch in str(text):
        if ch in _MD2_CHARS:
            result.append("\\" + ch)
        else:
            result.append(ch)
    return "".join(result)


class Notifier:
    def __init__(self, config):
        self._token = config.TELEGRAM_BOT_TOKEN
        self._chat_id = config.TELEGRAM_CHAT_ID
        self._dry_run = config.DRY_RUN

    async def send_episode_alert(self, episode) -> bool:
        title = _esc(episode.title)
        url = _esc(episode.youtube_url)

        msg = (
            f"*NEW JRE EPISODE DETECTED* 🎙️\n\n"
            f"*{title}*\n"
            f"{url}\n\n"
            f"_Starting keyword scan\\.\\.\\._"
        )
        return await self._send(msg)

    async def send_keyword_alert(
        self, hit: KeywordHit, episode, source: str
    ) -> bool:
        word = _esc(hit.term.upper())
        first_ts = _esc(hit.first_timestamp_str)
        count = _esc(str(hit.mention_count))
        snippet = _esc(hit.context_snippet[:120])
        src = _esc(source)

        msg = (
            f"*JRE KEYWORD ALERT* 🚨\n\n"
            f"*WORD:* {word}\n"
            f"*RESULT:* YES\n"
            f"*FIRST MENTION:* {first_ts}\n"
            f"*MENTIONS SO FAR:* {count}\n\n"
            f"*CONTEXT:*\n"
            f'_"\\.\\.\\. {snippet} \\.\\.\\."_\n\n'
            f"*SOURCE:*\n"
            f"{src}"
        )
        return await self._send(msg)

    async def send_test_alert(self) -> bool:
        msg = (
            "*JRE Monitor — Test Alert* ✅\n\n"
            "Bot is configured correctly\\!\n\n"
            "You will receive:\n"
            "1\\. *Episode detected* — when a new JRE uploads\n"
            "2\\. *Keyword alerts* — per keyword, as soon as it's spoken\n\n"
            "_Edit `keywords\\.json` to update your tracked terms\\._"
        )
        return await self._send(msg)

    async def _send(self, message: str) -> bool:
        if self._dry_run:
            logger.info(f"[DRY RUN] Telegram message:\n{message[:500]}")
            return True

        url = TELEGRAM_URL.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }

        for attempt in range(1, 4):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status == 200:
                            logger.info("Telegram alert sent")
                            return True
                        body = await resp.text()
                        logger.warning(
                            f"Telegram error {resp.status} (attempt {attempt}): "
                            f"{body[:200]}"
                        )
            except aiohttp.ClientError as e:
                logger.warning(f"Telegram network error (attempt {attempt}): {e}")

            if attempt < 3:
                await asyncio.sleep(2 * attempt)

        logger.error("Failed to send Telegram message after 3 attempts")
        return False
