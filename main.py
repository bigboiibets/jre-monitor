"""
main.py — Entry point for the JRE Keyword Monitor.

Commands:
    python main.py                  Start monitoring (runs forever)
    python main.py --dry-run        Run without sending real Telegram messages
    python main.py --test-alert     Send a test Telegram notification and exit
"""

import argparse
import asyncio
import logging
import sys


logger = logging.getLogger(__name__)


async def detection_loop(config, storage, coordinator, notifier):
    """
    Main polling loop. Checks all detection sources every POLL_INTERVAL_SECONDS.
    When a new episode is found, fires an alert and starts the race pipeline.

    Title-based dedup: if both YouTube RSS and Podcast RSS detect the same
    episode (slightly different IDs but same title prefix), only one pipeline runs.
    """
    from detectors.youtube_rss import YouTubeRSSDetector
    from detectors.podcast_rss import PodcastRSSDetector

    yt_detector = YouTubeRSSDetector(config.JRE_YOUTUBE_CHANNEL_ID)
    pod_detector = PodcastRSSDetector(config.JRE_PODCAST_RSS_URL)

    logger.info(
        f"Monitoring started — polling every {config.POLL_INTERVAL_SECONDS}s. "
        "Press Ctrl+C to stop."
    )

    # In-memory title dedup for episodes already processing this session
    active_title_prefixes: set[str] = set()

    # Seed from DB so we don't re-process episodes from before a restart
    for title in await storage.get_recent_episode_titles():
        active_title_prefixes.add(title[:40].lower())

    active_pipeline_task: asyncio.Task | None = None

    while True:
        try:
            yt_results, pod_results = await asyncio.gather(
                yt_detector.check(),
                pod_detector.check(),
                return_exceptions=True,
            )

            all_episodes = []
            if isinstance(yt_results, list):
                all_episodes.extend(yt_results)
            if isinstance(pod_results, list):
                all_episodes.extend(pod_results)

            for episode in all_episodes:
                title_key = episode.title[:40].lower()

                # Skip if we're already processing this episode
                if title_key in active_title_prefixes:
                    logger.debug(f"Already active: {episode.title}")
                    continue

                is_new = await storage.add_episode(
                    episode.episode_id,
                    episode.title,
                    episode.youtube_url,
                    episode.source,
                )

                if is_new:
                    active_title_prefixes.add(title_key)
                    logger.info(
                        f"NEW EPISODE DETECTED: {episode.title}  "
                        f"[source={episode.source}]"
                    )

                    await notifier.send_episode_alert(episode)

                    # Cancel any stale pipeline (shouldn't happen normally)
                    if active_pipeline_task and not active_pipeline_task.done():
                        logger.warning("Cancelling previous pipeline for new episode")
                        active_pipeline_task.cancel()

                    active_pipeline_task = asyncio.create_task(
                        coordinator.run(episode),
                        name=f"pipeline_{episode.episode_id}",
                    )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Detection loop error: {e}", exc_info=True)

        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)


async def async_main():
    parser = argparse.ArgumentParser(
        description="JRE Keyword Monitor — real-time keyword detection for prediction markets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                   Start monitoring (runs until Ctrl+C)
  python main.py --dry-run         Test run — logs alerts but doesn't send to Telegram
  python main.py --test-alert      Send a test message to verify your Telegram setup
        """,
    )
    parser.add_argument(
        "--test-alert", action="store_true",
        help="Send a test Telegram alert and exit",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run without sending real Telegram messages (logs them instead)",
    )
    args = parser.parse_args()

    # Load config
    try:
        from config import Settings
        config = Settings()
    except Exception as e:
        print(f"\nERROR: Failed to load configuration: {e}", file=sys.stderr)
        print(
            "Make sure .env exists in the current directory. "
            "Copy .env.example to .env and fill in your values.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.dry_run:
        config.DRY_RUN = True

    # Set up logging
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from notifier import Notifier
    notifier = Notifier(config)

    # Test alert mode — send message and exit
    if args.test_alert:
        ok = await notifier.send_test_alert()
        print("Test alert sent! Check your Telegram." if ok else "Failed to send test alert.")
        return

    # Validate required config
    if not config.JRE_YOUTUBE_CHANNEL_ID:
        print(
            "\nERROR: JRE_YOUTUBE_CHANNEL_ID is not set in .env\n"
            "Find it at https://www.youtube.com/@joerogan → view page source → "
            "search for 'channelId'",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load Whisper model at startup (blocking, ~2–5s)
    from pipeline.transcriber import WhisperTranscriber
    transcriber = WhisperTranscriber(
        model_size=config.WHISPER_MODEL,
        chunk_seconds=config.CHUNK_SECONDS,
    )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, transcriber.load_model)

    from keyword_engine import KeywordEngine
    from race.coordinator import RaceCoordinator
    from storage import Storage

    keyword_engine = KeywordEngine(config.KEYWORDS_FILE, config.DEDUP_WINDOW_MINUTES)

    print(f"\nJRE Keyword Monitor")
    print(f"  Channel ID:      {config.JRE_YOUTUBE_CHANNEL_ID}")
    print(f"  Poll interval:   {config.POLL_INTERVAL_SECONDS}s")
    print(f"  Whisper model:   {config.WHISPER_MODEL}")
    print(f"  Chunk size:      {config.CHUNK_SECONDS}s")
    print(f"  Dedup window:    {config.DEDUP_WINDOW_MINUTES}min")
    print(f"  Keywords file:   {config.KEYWORDS_FILE}")
    print(f"  Database:        {config.DB_PATH}")
    print(f"  Dry run:         {config.DRY_RUN}")
    print(f"\nPress Ctrl+C to stop.\n")

    async with Storage(config.DB_PATH) as storage:
        coordinator = RaceCoordinator(
            config, storage, transcriber, keyword_engine, notifier
        )
        await detection_loop(config, storage, coordinator, notifier)


def main():
    # Windows requires ProactorEventLoop for asyncio subprocess support
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    # Fix Windows console encoding for non-ASCII transcript text
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


if __name__ == "__main__":
    main()
