# ebook-translator

[![CI](https://github.com/Tabasiarash/ebook-translator/actions/workflows/ci.yml/badge.svg)](https://github.com/Tabasiarash/ebook-translator/actions/workflows/ci.yml)

PDF ebook translator Telegram bot that translates large books via rotating free-tier LLM pools, preserves visual layout, and downloads video/media from YouTube, Instagram, Twitter/X, Facebook, TikTok, LinkedIn, and Snapchat — plus generic URL relay and audiobook TTS generation.

## Status

Active

## Features

- **9-platform video download**: YouTube, Instagram, Twitter/X, Facebook, TikTok, LinkedIn, Snapchat — route via URL to yt-dlp or direct queue, quality picker, media groups for carousels
- **PDF ebook translation**: Preserves original layout (PyMuPDF in-place edit for same-script pairs, WeasyPrint regeneration for RTL/CJK)
- **SSRF-safe URL relay**: `/fetch` command with IP block, redirect validation, size caps, per-user cooldown
- **Audiobook TTS**: edge-tts integration with language-to-voice mapping (15+ languages), per-segment caching, combined MP3 output
- **Redis-backed work queue**: Stream consumer groups with ack/pending/claim for restart safety
- **Provider pool rotation**: Cooldown zset tracks per-key rate limits; sleeps until next available rather than dropping work
- **Glossary consistency**: Upfront LLM pass extracts proper nouns/invented terms per-chapter; injected into per-chunk prompts

## Architecture

```
User → Telegram → bot.py → Redis (download:pending, fetch:pending)
                            ├── download_worker → yt-dlp → local Bot API upload
                            ├── fetch_worker    → aiohttp → trafilatura → text relay
                            └── ingest/translate/reassemble/reconcile workers (ebook pipeline)
```

All workers are long-running Python processes reading from Redis Streams with consumer groups. The bot registers commands, queues jobs, and listens for pub/sub completion notifications.

## Requirements

- Python 3.12+
- Redis 7+
- Local Telegram Bot API server (optional, for large file uploads)
- ffmpeg (for yt-dlp post-processing)
- System deps for WeasyPrint: `libpango-1.0-0`, `libpangocairo-1.0-0`, `libgdk_pixbuf2.0-0`, `libffi-dev`, `shared-mime-info`

## Setup

```bash
git clone https://github.com/Tabasiarash/ebook-translator.git
cd ebook-translator

python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your TELEGRAM_BOT_TOKEN and REDIS_URL

mkdir -p fonts logs
# Place font files in fonts/ if using Mode B rendering (see AGENTS.md)
```

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from @BotFather |
| `TELEGRAM_API_BASE_URL` | No | `https://api.telegram.org` | Custom Bot API server base URL |
| `TELEGRAM_API_FILE_BASE_URL` | No | `https://api.telegram.org` | Custom Bot API file URL |
| `REDIS_URL` | Yes | `redis://127.0.0.1:6379/0` | Redis connection string (may include password) |
| `BASE_DIR` | No | `/var/www/ebook-translator` | Root directory for jobs, DB, providers |
| `DB_PATH` | No | `<BASE_DIR>/db.sqlite3` | SQLite database path |
| `PROVIDERS_PATH` | No | `<BASE_DIR>/providers.yaml` | LLM provider keys YAML file |
| `LOG_DIR` | No | `/var/log/ebook-translator` | Log output directory |
| `FONT_DIR` | No | `<BASE_DIR>/fonts` | Bundled fonts for Mode B rendering |
| `DOWNLOAD_TMP_DIR` | No | `/tmp/tgbot_downloads` | Temporary download staging directory |
| `COOKIES_FILE` | No | — | Path to cookies.txt for YouTube/restricted content |
| `MAX_UPLOAD_MB` | No | `1900` | Telegram upload limit in MB |
| `PER_USER_COOLDOWN_SECONDS` | No | `60` | Cooldown between downloads per user |
| `MAX_CONCURRENT_DOWNLOADS` | No | `3` | Max simultaneous downloads |
| `BOT_POLL_INTERVAL` | No | `5` | Telegram poll interval in seconds |
| `FETCH_TIMEOUT_SECONDS` | No | `45` | HTTP request timeout for /fetch |
| `FETCH_MAX_MB` | No | `100` | Max response size for /fetch |
| `FETCH_PER_USER_COOLDOWN_SECONDS` | No | `30` | Cooldown between /fetch calls per user |

## Usage

Send commands to the Telegram bot:

| Command | Description |
|---|---|
| `/start` `/menu` | Main menu navigation |
| `/download <url>` | Download video from supported platforms |
| `/fetch <url>` | Relay URL content (rss, article, image) with SSRF safeguards |
| `/cancel` | Cancel a download |
| `/cookies` | Upload cookies.txt for authenticated downloads |
| `/audiobook` | Generate TTS narration from translated book |
| `/status` `/review` `/resume` `/arch` | Ebook translator job management |

For video downloads, you can also paste a supported link directly without `/download`. Supported domains: youtube.com, youtu.be, instagram.com, twitter.com, x.com, facebook.com, fb.watch, tiktok.com, linkedin.com, snapchat.com.

## Deployment

The project runs as systemd services on a VPS. Systemd unit files are installed at `/etc/systemd/system/`:

```bash
# Enable and start
systemctl daemon-reload
systemctl enable ebook-bot.service ebook-download.service ebook-fetch.service
systemctl enable ebook-ingest.service ebook-reassemble.service ebook-reconcile.service
systemctl enable ebook-translate@1.service ebook-translate@2.service ebook-translate@3.service

systemctl start ebook-bot.service ebook-download.service ebook-fetch.service
systemctl start ebook-ingest.service ebook-reassemble.service ebook-reconcile.service
systemctl start ebook-translate@1.service ebook-translate@2.service ebook-translate@3.service
```

Available services and their purpose:

| Service | Purpose |
|---|---|
| `ebook-bot.service` | Main Telegram bot (handler registration, menu, commands) |
| `ebook-download.service` | Video download worker (all 9 platforms) |
| `ebook-fetch.service` | Generic URL relay worker (`/fetch`) |
| `ebook-ingest.service` | PDF ingest + glossary extraction |
| `ebook-translate@.service` | Translate worker (instantiated for concurrency) |
| `ebook-reassemble.service` | Render + deliver translated PDF |
| `ebook-reconcile.service` | Periodic stuck-job detection and recovery |

Each service reads from `/root/ebook-translator/.env` and logs to `/var/log/ebook-translator/`.

## Development

```bash
source venv/bin/activate

# Run all tests (143 tests at time of writing)
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_video_download.py -v

# Lint
ruff check .
ruff format --check .

# Type check
mypy ebook_translator/ bot/
```

### Test structure

| File | Tests | Coverage |
|---|---|---|
| `tests/test_pdf.py` | 18 | PDF profile extraction, chunking, sentence boundaries |
| `tests/test_providers.py` | 12 | Key loading, cooldown, pick_available, effective RPM |
| `tests/test_glossary.py` | 8 | Glossary extraction and injection |
| `tests/test_render.py` | 12 | Mode A font shrink, Mode B HTML/CSS generation |
| `tests/test_queue.py` | 7 | Redis stream connect, groups, read/pending |
| `tests/test_pipeline.py` | 3 | Full pipeline end-to-end |
| `tests/test_video_download.py` | 28 | URL pattern, domain routing, engine selection |
| `tests/test_download_worker.py` | 10 | Failure notification messages, file classification |
| `tests/test_fetch_handler.py` | 18 | URL regex, SSRF IP blocking, input validation |
| `tests/test_tts.py` | 22 | Voice mapping, segment concatenation, duration estimation |

### Commit conventions

Prefix commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`. Update `README.md`, `AGENTS.md` "Current state", and `CHANGELOG.md` in the same commit as the change. Every session ends with a push. No force-push on shared branches.

## Known limitations

- **LinkedIn/Snapchat downloads**: yt-dlp extractors for these platforms are limited — downloads may fail. User sees a "limited support" message.
- **TikTok**: May block datacenter IPs — cookies may help.
- **Instagram carousels**: Supported via media groups, but mixed video/photo carousels may have ordering issues.
- **YouTube JS challenge**: Requires cookies file + `--js-runtimes node` flag.
- **Mode B rendering**: Some PDFs with complex vector backgrounds or form fields may not reproduce perfectly.
- **Glossary extraction**: Relies on the LLM correctly identifying proper nouns — may miss domain-specific terms.

## License

MIT — see [LICENSE](LICENSE).