"""
race/coordinator.py — Orchestrates the two-arm race pipeline per episode.

Two tasks run in parallel for each detected episode:

  ARM A: Live audio transcription
         yt-dlp → ffmpeg segments → faster-whisper → keyword scan
         Starts as soon as the episode is detected. Latency is bounded
         by audio acquisition + chunk duration + transcription time.

  ARM B: Transcript site polling
         Polls podscripts.co every 60s. Slower but requires no audio
         processing. Useful if ARM A stalls or misses something.

Both arms share the same KeywordEngine instance, which handles dedup so
the same keyword never fires two alerts from competing sources.

ARM A signals completion via a done_event so ARM B knows when to stop
polling (the episode is over).
"""

import asyncio
import logging

from detectors.youtube_rss import Episode
from detectors.transcript_site import TranscriptSiteDetector
from pipeline.audio_fetcher import get_audio_url
from pipeline.segmenter import stream_and_segment
from pipeline.transcriber import WhisperTranscriber
from keyword_engine import KeywordEngine
from notifier import Notifier
from latency_logger import LatencyLogger

logger = logging.getLogger(__name__)

# How often the transcript site arm polls (seconds)
TRANSCRIPT_SITE_POLL_INTERVAL = 60


class RaceCoordinator:
    def __init__(
        self,
        config,
        storage,
        transcriber: WhisperTranscriber,
        keyword_engine: KeywordEngine,
        notifier: Notifier,
    ):
        self.config = config
        self.storage = storage
        self.transcriber = transcriber
        self.keyword_engine = keyword_engine
        self.notifier = notifier

    async def run(self, episode: Episode):
        """
        Run both race arms for a detected episode and wait for both to finish.
        The done_event coordinates shutdown: ARM A sets it when audio ends,
        ARM B uses it as a stop signal.
        """
        self.keyword_engine.reset()
        lat = LatencyLogger(episode.episode_id)
        lat.mark("episode_pipeline_started")

        live_done_event = asyncio.Event()

        task_live = asyncio.create_task(
            self._live_transcription_arm(episode, lat, live_done_event),
            name=f"live_{episode.episode_id}",
        )
        task_site = asyncio.create_task(
            self._transcript_site_arm(episode, lat, live_done_event),
            name=f"site_{episode.episode_id}",
        )

        try:
            results = await asyncio.gather(
                task_live, task_site, return_exceptions=True
            )
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    arm_name = "live" if i == 0 else "site"
                    logger.error(f"Race arm '{arm_name}' raised: {result}")
        except asyncio.CancelledError:
            task_live.cancel()
            task_site.cancel()
            raise
        finally:
            lat.report()
            await lat.flush_to_db(self.storage)
            await self.storage.update_episode_status(episode.episode_id, "complete")

    # -------------------------------------------------------------------------
    # ARM A: Live audio transcription
    # -------------------------------------------------------------------------

    async def _live_transcription_arm(
        self, episode: Episode, lat: LatencyLogger, done_event: asyncio.Event
    ):
        logger.info(f"[ARM A] Starting live transcription for: {episode.title}")

        try:
            # Step 1: get the direct audio stream URL
            audio_url = await get_audio_url(episode.youtube_url)
            if not audio_url:
                logger.error("[ARM A] Could not get audio URL — live arm exiting")
                return

            lat.mark("audio_url_acquired")
            await self.storage.update_episode_status(episode.episode_id, "streaming")

            chunk_index = 0
            first_chunk_done = False

            # Step 2: stream + segment audio, transcribe each chunk
            async for chunk_path in stream_and_segment(
                audio_url,
                self.config.FFMPEG_PATH,
                self.config.CHUNK_SECONDS,
            ):
                if not first_chunk_done:
                    lat.mark("first_chunk_ready")

                segments = await self.transcriber.transcribe_chunk(
                    chunk_path, chunk_index
                )

                if not first_chunk_done:
                    lat.mark("first_transcription_complete")
                    first_chunk_done = True

                # Step 3: scan each segment for keywords
                for seg in segments:
                    if not seg.text:
                        continue

                    await self.storage.log_segment(
                        episode.episode_id, seg.start, seg.text, "whisper"
                    )

                    hits = self.keyword_engine.process_segment(seg.text, seg.start)
                    for hit in hits:
                        hit.source = "LIVE TRANSCRIPTION"
                        lat.mark(
                            f"keyword_hit_{hit.term}",
                            f"at={hit.first_timestamp_str}",
                        )
                        ok = await self.notifier.send_keyword_alert(
                            hit, episode, "LIVE TRANSCRIPTION"
                        )
                        lat.mark(f"alert_sent_{hit.term}")
                        await self.storage.log_keyword_alert(
                            episode.episode_id,
                            hit.term,
                            hit.first_timestamp_str,
                            hit.mention_count,
                            hit.context_snippet,
                            "LIVE TRANSCRIPTION",
                            ok,
                        )

                chunk_index += 1

            logger.info(
                f"[ARM A] Audio pipeline complete. "
                f"Processed {chunk_index} chunks."
            )

        except asyncio.CancelledError:
            logger.info("[ARM A] Cancelled")
            raise
        except Exception as e:
            logger.error(f"[ARM A] Unexpected error: {e}", exc_info=True)
        finally:
            done_event.set()  # signal ARM B that live transcription is done

    # -------------------------------------------------------------------------
    # ARM B: Transcript site polling
    # -------------------------------------------------------------------------

    async def _transcript_site_arm(
        self,
        episode: Episode,
        lat: LatencyLogger,
        live_done_event: asyncio.Event,
    ):
        logger.info(f"[ARM B] Starting transcript site polling for: {episode.title}")
        detector = TranscriptSiteDetector()

        while not live_done_event.is_set():
            segments = await detector.fetch(episode)

            if segments:
                lat.mark("transcript_site_available", f"segments={len(segments)}")
                logger.info(f"[ARM B] Transcript available — scanning {len(segments)} segments")

                for seg in segments:
                    text = seg.get("text", "")
                    start = seg.get("start", 0.0)
                    if not text:
                        continue

                    await self.storage.log_segment(
                        episode.episode_id, start, text, "transcript_site"
                    )

                    hits = self.keyword_engine.process_segment(text, start)
                    for hit in hits:
                        hit.source = "TRANSCRIPT SITE"
                        lat.mark(
                            f"site_keyword_hit_{hit.term}",
                            f"at={hit.first_timestamp_str}",
                        )
                        ok = await self.notifier.send_keyword_alert(
                            hit, episode, "TRANSCRIPT SITE"
                        )
                        lat.mark(f"site_alert_sent_{hit.term}")
                        await self.storage.log_keyword_alert(
                            episode.episode_id,
                            hit.term,
                            hit.first_timestamp_str,
                            hit.mention_count,
                            hit.context_snippet,
                            "TRANSCRIPT SITE",
                            ok,
                        )

                # Transcript found — no need to keep polling
                logger.info("[ARM B] Transcript site scan complete")
                return

            # Wait before polling again, but stop early if ARM A finishes
            try:
                await asyncio.wait_for(
                    live_done_event.wait(),
                    timeout=float(TRANSCRIPT_SITE_POLL_INTERVAL),
                )
            except asyncio.TimeoutError:
                pass  # normal — just means we waited the full interval

        logger.info("[ARM B] Stopping — live transcription arm has finished")
