"""
pipeline/transcriber.py — Transcribes audio chunks using faster-whisper.

faster-whisper is CPU-bound and blocking, so each call runs in an asyncio
thread executor to avoid blocking the event loop.

The model is loaded ONCE at startup (takes 1–5 seconds) and reused for
every chunk. Calling load_model() in main.py before the event loop starts
is strongly recommended.

Model size guide (CPU performance on a modern laptop):
  tiny.en  — ~1–2s per 25s chunk, lower accuracy
  base.en  — ~2–4s per 25s chunk, good accuracy  ← default
  small.en — ~5–10s per 25s chunk, best accuracy

int8 quantisation is used by default — halves memory and speeds inference
with negligible accuracy loss on English speech.
"""

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TranscriptSegment:
    text: str
    start: float      # seconds from start of episode
    duration: float


class WhisperTranscriber:
    def __init__(self, model_size: str = "base.en", chunk_seconds: int = 25):
        self._model_size = model_size
        self._chunk_seconds = chunk_seconds
        self._model = None

    def load_model(self):
        """
        Load the Whisper model into memory. Blocking — call this once before
        starting the asyncio event loop (or in run_in_executor at startup).
        """
        from faster_whisper import WhisperModel
        logger.info(f"Loading Whisper model '{self._model_size}' (this takes a moment)...")
        self._model = WhisperModel(
            self._model_size,
            device="cpu",
            compute_type="int8",
        )
        logger.info("Whisper model ready")

    async def transcribe_chunk(
        self,
        chunk_path: str,
        chunk_index: int,
        episode_offset_seconds: float = 0.0,
    ) -> list[TranscriptSegment]:
        """
        Transcribe a single .wav chunk asynchronously.

        chunk_index is used to compute episode-relative timestamps
        (chunk_index * chunk_seconds + segment.start).
        """
        if self._model is None:
            raise RuntimeError("Call load_model() before transcribing")

        chunk_start = episode_offset_seconds + (chunk_index * self._chunk_seconds)

        loop = asyncio.get_running_loop()
        raw_segments = await loop.run_in_executor(
            None, self._transcribe_sync, chunk_path
        )

        result: list[TranscriptSegment] = []
        for seg in raw_segments:
            text = seg.text.strip()
            if text:
                result.append(TranscriptSegment(
                    text=text,
                    start=chunk_start + seg.start,
                    duration=seg.end - seg.start,
                ))

        return result

    def _transcribe_sync(self, audio_path: str) -> list:
        """
        Blocking transcription call — always called inside run_in_executor.
        Consumes the faster-whisper generator here (in the worker thread).
        """
        segments, _ = self._model.transcribe(
            audio_path,
            language="en",
            beam_size=1,       # fastest; beam_size=5 is more accurate but slower
            vad_filter=True,   # skip silent sections for speed
            vad_parameters={"min_silence_duration_ms": 500},
        )
        return list(segments)  # must materialise the generator in this thread
