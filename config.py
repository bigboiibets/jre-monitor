"""
config.py — All configuration via .env (pydantic-settings).

Copy .env.example to .env and fill in your values before running.
Required: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, JRE_YOUTUBE_CHANNEL_ID
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # -------------------------------------------------------------------------
    # Required
    # -------------------------------------------------------------------------
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str

    # -------------------------------------------------------------------------
    # Episode detection
    # -------------------------------------------------------------------------
    # YouTube channel ID for JRE (PowerfulJRE). Find it at:
    # https://www.youtube.com/@joerogan → view page source → search "channelId"
    JRE_YOUTUBE_CHANNEL_ID: str = ""

    # JRE podcast RSS feed (Megaphone-hosted). Leave blank to disable.
    JRE_PODCAST_RSS_URL: str = "https://feeds.megaphone.fm/GLT1412515089"

    # How often to poll detection sources (seconds)
    POLL_INTERVAL_SECONDS: int = 30

    # -------------------------------------------------------------------------
    # Audio & transcription
    # -------------------------------------------------------------------------
    # Duration of each audio chunk sent to Whisper (seconds)
    # Smaller = lower latency but more overhead. 20–30s is optimal.
    CHUNK_SECONDS: int = 25

    # Whisper model size. Tradeoff: speed vs accuracy.
    # Options: tiny.en (fastest) | base.en | small.en (most accurate on CPU)
    WHISPER_MODEL: str = "base.en"

    # Path to ffmpeg binary. On Windows, either add ffmpeg to PATH or put
    # ffmpeg.exe in the project directory and set this to "ffmpeg.exe"
    FFMPEG_PATH: str = "ffmpeg"

    # -------------------------------------------------------------------------
    # Keyword engine
    # -------------------------------------------------------------------------
    KEYWORDS_FILE: str = "keywords.json"

    # Suppress repeat alerts for the same keyword within this window (minutes)
    DEDUP_WINDOW_MINUTES: int = 10

    # -------------------------------------------------------------------------
    # Storage
    # -------------------------------------------------------------------------
    DB_PATH: str = "jre_monitor.db"

    # -------------------------------------------------------------------------
    # Misc
    # -------------------------------------------------------------------------
    DRY_RUN: bool = False
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
