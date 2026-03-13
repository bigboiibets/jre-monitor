# CLAUDE.md — JRE Keyword Monitor

## Commands

```bash
# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Test Telegram connection
python main.py --test-alert

# Dry run (no Telegram messages sent, alerts logged to console)
python main.py --dry-run

# Run the monitor
python main.py
```

## Architecture

Full async pipeline (`asyncio`). No threads except for Whisper inference (CPU-bound, runs in executor).

### Data flow

```
main.py               →  event loop, detection loop, wires everything together
detectors/
  youtube_rss.py      →  polls YouTube RSS every 30s for new JRE videos
  podcast_rss.py      →  polls podcast RSS (parallel, secondary detection path)
  transcript_site.py  →  scrapes podscripts.co (ARM B of race)
pipeline/
  audio_fetcher.py    →  yt-dlp subprocess → extracts audio stream URL
  segmenter.py        →  ffmpeg subprocess → temp dir watcher → yields .wav chunks
  transcriber.py      →  faster-whisper in thread executor → TranscriptSegment list
race/
  coordinator.py      →  ARM A (live transcription) + ARM B (transcript site) in parallel
keyword_engine.py     →  scans text segments, dedup logic, returns KeywordHit list
notifier.py           →  Telegram MarkdownV2 alerts (async, retries)
latency_logger.py     →  records named timing events, prints report, flushes to DB
storage.py            →  aiosqlite wrapper, all DB reads/writes
config.py             →  pydantic-settings, reads from .env
```

### Race architecture

Two asyncio Tasks run per episode:
- **ARM A**: audio_fetcher → segmenter → transcriber → keyword_engine (live, chunk by chunk)
- **ARM B**: polls transcript_site every 60s → keyword_engine (fallback)

Both share the same `KeywordEngine` instance. The dedup window prevents duplicate alerts when both arms find the same keyword. ARM A sets a `done_event` when audio finishes; ARM B uses it as a stop signal.

### Key design decisions

- **Windows ProactorEventLoop** — set in `main()` before `asyncio.run()`. Required for `asyncio.create_subprocess_exec` on Windows.
- **Whisper in executor** — `model.transcribe()` is blocking/CPU-bound; always runs in `loop.run_in_executor(None, ...)` to not block the event loop.
- **Chunk readiness detection** — chunk N is emitted only after chunk N+1 appears in the temp dir (ffmpeg closes N before opening N+1). Prevents reading a partially-written WAV.
- **Keywords reload on every segment** — live keyword editing while bot is running.
- **`INSERT OR IGNORE`** idempotency — safe to restart mid-episode.
- **Title-prefix dedup** — prevents dual-detection (YouTube RSS + podcast RSS same episode) from spawning two pipelines.

### SQLite schema

Four tables in `jre_monitor.db`:
- `episodes` — one row per detected episode, tracks status
- `keyword_alerts` — every alert fired, with source and Telegram success flag
- `transcript_segments` — full transcript accumulated during live transcription
- `latency_events` — timing events for every pipeline stage

### Config

All values in `.env` (copy from `.env.example`). Required: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `JRE_YOUTUBE_CHANNEL_ID`.

### External dependencies (not pip)

- **ffmpeg** — must be installed separately. Download from https://ffmpeg.org/download.html
  and either add to PATH or set `FFMPEG_PATH=path\to\ffmpeg.exe` in `.env`.
- **yt-dlp** — installed via pip but requires internet access and a valid YouTube URL.
