# Agent Context

## What this project is

PDF ebook translator Telegram bot that translates large books via rotating free-tier LLM pool, preserves visual layout, and downloads video/media from YouTube, Instagram, Twitter/X, Facebook, TikTok, LinkedIn, Snapchat — plus generic URL relay (`/fetch`) and audiobook TTS generation. All workers are long-running Python processes reading from Redis Streams with consumer groups.

## Architecture

```
ebook-translator/
├── bot.py                          # Main entry: handler registration, menu, main loop
├── bot/
│   ├── handlers/
│   │   ├── video_download.py       # URL regex, get_domain_engine(), quality picker
│   │   ├── fetch.py                # /fetch command with SSRF safeguards
│   │   └── cookie_login.py         # /cookies command for user cookie upload
│   ├── services/
│   │   └── downloader.py           # yt-dlp commands, probe, format selector
│   └── workers/
│       ├── download_worker.py      # Redis consumer: _handle_youtube/instagram/twitter
│       └── fetch_worker.py         # Redis consumer: aiohttp + trafilatura extraction
├── ebook_translator/
│   ├── config.py                   # Settings dataclass, all env vars
│   ├── db.py                       # SQLite helpers (async, fetchone/fetchall)
│   ├── pdf.py                      # PDF profile extraction, font metadata, chunking
│   ├── render.py                   # Mode A (PyMuPDF in-place) / Mode B (WeasyPrint)
│   ├── providers.py                # Provider key loading, cooldown, rate limit
│   ├── queue.py                    # Redis Stream connect/ensure_group/read_group
│   ├── tts.py                      # edge-tts wrapper, voice map, concatenation
│   ├── eta.py                      # ETA estimation
│   ├── languages.py                # Language pairs, mode selection
│   └── logging_config.py           # JSON-structured log config
├── ingest_worker.py                # PDF ingest + glossary extraction pipeline
├── translate_worker.py             # Chunk translation with glossary injection
├── reassemble_worker.py            # Completion check + render + pub/sub notification
├── reconcile_worker.py             # Periodic stuck-job detection
├── tests/
│   ├── conftest.py                 # Shared fixtures (redis_conn, mock_providers_yaml)
│   ├── test_pdf.py                 # 18 tests: profile, chunks, sentence boundaries
│   ├── test_providers.py           # 12 tests: key loading, cooldown, pick_available
│   ├── test_glossary.py            # 8 tests: glossary extraction and injection
│   ├── test_render.py              # 12 tests: Mode A/B rendering, font shrink
│   ├── test_queue.py               # 7 tests: Redis stream connect/group/read
│   ├── test_pipeline.py            # 3 tests: full pipeline end-to-end
│   ├── test_video_download.py      # 28 tests: URL pattern, domain routing, engine
│   ├── test_download_worker.py     # 10 tests: _notify_fail messages, classification
│   ├── test_fetch_handler.py       # 18 tests: URL regex, SSRF IP blocking
│   └── test_tts.py                 # 22 tests: voice map, concat, estimate
├── scripts/
│   └── refresh_cookies.py          # Chrome cookie decryption + hourly refresh
├── providers.yaml                  # LLM provider keys (gemini, groq, etc.)
├── requirements.txt
├── .env.example
├── CHANGELOG.md
├── LICENSE
└── README.md
```

## Conventions

- **Code style**: Python 3.12+, f-strings preferred, type annotations everywhere, no docstrings on trivial methods, no comments unless explaining a non-obvious decision.
- **Imports**: stdlib first, third-party second, local third. Group with blank lines.
- **Logging**: JSON-structured via `configure_logging()` — `log = logging.getLogger(__name__)`, then `log.info("msg", extra={...})`.
- **Async**: All I/O is async (redis-py asyncio, aiohttp, edge-tts, python-telegram-bot v20+). Sync functions for CPU-bound work (PDF rendering, font analysis).
- **Testing**: `pytest` with `pytest-asyncio` (mode=AUTO). Fixtures in `conftest.py`. Test DB is a temp SQLite, Redis is shared (cooldown zset cleaned per test via `clean_cooldowns`).
- **Error handling**: Never catch bare `Exception` in worker dispatch; log and ack the message to avoid blocking the stream.

## Current state

### Built and working
- All 9 platform download routes: YouTube, Instagram, Twitter/X, Facebook, TikTok, LinkedIn, Snapchat
- `/fetch` command with SSRF-safe HTTP client (IP block, size cap, redirect validation, per-user cooldown)
- Redis-backed work queue with consumer groups across all workers
- PDF translation pipeline: ingest → glossary extraction → translate → reassemble
- Mode A (PyMuPDF in-place) for same-script pairs, Mode B (WeasyPrint) for RTL/CJK
- Audiobook TTS via edge-tts with 15+ language voices
- Full test suite: 143 tests, all passing
- All services deployed via systemd on VPS 187.124.9.186

### Partially built
- Provider pool rotation cooldown tracking uses sorted set — no penalty-based fallback yet
- `/review` command exists but is basic (limited to failed chunks display)
- No active monitoring/alerting (no health check endpoint, no prometheus metrics)

### Not started
- No `/fetch` content summarization (LLM-based summarization of fetched articles)
- No concurrent translate workers scaling based on queue depth
- No user authentication / per-user job quotas

## Known issues / upstream dependencies

- **LinkedIn/Snapchat downloads**: yt-dlp extractors are partial — `_handle_youtube` returns `"best_effort"` failure message instead of generic one. Not fixable from this codebase.
- **YouTube JS challenge**: Requires `--js-runtimes node` and valid cookies — if cookies expire, downloads fail silently.
- **Instagram**: yt-dlp PR #17075 (Jun 2026) reworked the extractor; relies on upstream being stable.
- **Redis**: Event loop is closed warning during test teardown — benign, caused by redis-py destructor running after asyncio loop closes.
- **`.env` file**: `/root/ebook-translator/.env` contains live secrets — never commit it.

## Workflow policy

- Conventional commit prefixes: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`
- Every session that changes code ends with commit + push to GitHub
- README.md, AGENTS.md "Current state", and CHANGELOG.md updated in the same commit as the change that necessitates it
- No force-push or history rewriting on pushed commits
- Incomplete/WIP features committed as clearly marked WIP — never presented as done

## Key operational facts

- Bot token (live): `8001452590:AAGEHtLFG5riMFxEeOwVN_plR9Uawiwc9TY`
- Bot API server: port 8082 at `telegram-bot-api.service`
- Cookies: `/root/yt_cookies.txt` decrypted from Chrome Profile 4
- Cookie refresh: `scripts/refresh_cookies.py` runs hourly via cron
- Redis: `redis://:a3e0a2371ea2df1a047e584396902aa7ef65c06366ad185770dd3a1b9362bc4a@127.0.0.1:6379/0` (password abbreviated)
- GitHub remote: `https://github.com/Tabasiarash/ebook-translator.git` (HTTPS with PAT in remote URL)
