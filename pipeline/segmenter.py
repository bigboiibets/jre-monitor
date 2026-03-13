"""
pipeline/segmenter.py — Segments a streaming audio URL into fixed-length .wav chunks.

ffmpeg reads the audio URL directly (no full download required) and writes
numbered .wav files to a temp directory. A watcher loop detects when each
chunk is complete and yields its path.

A chunk is considered complete once ffmpeg has started writing the NEXT chunk
(because ffmpeg holds the current file open until it moves on). This avoids
reading a partially-written WAV file.

Output format: 16kHz mono PCM — exactly what faster-whisper expects.
"""

import asyncio
import logging
import os
import shutil
import tempfile
from typing import AsyncGenerator

logger = logging.getLogger(__name__)


async def stream_and_segment(
    audio_url: str,
    ffmpeg_path: str,
    chunk_seconds: int,
) -> AsyncGenerator[str, None]:
    """
    Async generator that yields complete .wav chunk file paths in order.

    Launches ffmpeg as a background subprocess. The temp directory is always
    cleaned up when the generator exits (even on cancellation or error).
    """
    temp_dir = tempfile.mkdtemp(prefix="jre_chunks_")
    logger.info(f"Segmenting audio into {chunk_seconds}s chunks → {temp_dir}")

    output_pattern = os.path.join(temp_dir, "chunk_%04d.wav")

    ffmpeg_cmd = [
        ffmpeg_path,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", audio_url,
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-ar", "16000",         # 16kHz — required by Whisper
        "-ac", "1",             # mono
        "-c:a", "pcm_s16le",    # raw PCM, no re-encoding latency
        "-reset_timestamps", "1",
        "-y",                   # overwrite without prompting
        output_pattern,
    ]

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        async for chunk_path in _watch_chunks(temp_dir, proc):
            yield chunk_path

        await proc.wait()
        logger.info("ffmpeg finished — all chunks processed")

    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
        raise
    except FileNotFoundError:
        logger.error(
            f"ffmpeg not found at '{ffmpeg_path}'. "
            "Download from https://ffmpeg.org/download.html and add to PATH, "
            "or set FFMPEG_PATH in .env"
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.debug("Audio temp dir cleaned up")


async def _watch_chunks(
    temp_dir: str,
    proc: asyncio.subprocess.Process,
) -> AsyncGenerator[str, None]:
    """
    Polls the temp directory every 200ms. Yields chunk N once chunk N+1
    has appeared (meaning ffmpeg has closed chunk N and moved on).

    After ffmpeg exits, yields any remaining unprocessed chunks.
    """
    emitted: set[str] = set()

    while proc.returncode is None:
        await asyncio.sleep(0.2)

        try:
            wav_files = sorted(
                e.name
                for e in os.scandir(temp_dir)
                if e.name.endswith(".wav")
            )
        except OSError:
            continue

        # All chunks except the last are guaranteed complete
        complete = wav_files[:-1] if len(wav_files) > 1 else []

        for name in complete:
            if name not in emitted:
                emitted.add(name)
                full_path = os.path.join(temp_dir, name)
                if os.path.getsize(full_path) > 0:
                    logger.debug(f"Chunk ready: {name}")
                    yield full_path

    # ffmpeg has exited — yield any remaining chunks
    try:
        final_files = sorted(
            e.name for e in os.scandir(temp_dir) if e.name.endswith(".wav")
        )
    except OSError:
        final_files = []

    for name in final_files:
        if name not in emitted:
            full_path = os.path.join(temp_dir, name)
            if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                yield full_path
