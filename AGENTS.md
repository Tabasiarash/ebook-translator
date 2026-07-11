# Work State

## Objective
Telegram bot for YouTube + Instagram video downloads via local Bot API server.

## Architecture
- User sends URL → bot queues to Redis `download:pending` stream
- Download worker picks up, downloads via yt-dlp, uploads via local Bot API
- All services run via systemd on the same machine

## Key Facts
- Bot token: `8001452590:AAGEHtLFG5riMFxEeOwVN_plR9Uawiwc9TY`
- API IDs: `api_id=33282119`, `api_hash=c24cf53f30908e78cbaf76dd67214ac7`
- Bot API server: port 8082 at `telegram-bot-api.service`
- Cookies: `/root/yt_cookies.txt` decrypted from Chrome Profile 4
- Cookie refresh: `scripts/refresh_cookies.py` runs hourly via cron
- YouTube requires cookies + `--remote-components ejs:github --js-runtimes node` for JS challenge
- Worker auto-deletes files after upload (single `file_path.unlink()` for yt-dlp, `shutil.rmtree(job_dir)` for Instagram)

## Instagram Status
- **Working** (as of yt-dlp nightly 2026.07.09)
- yt-dlp PR #17075 (Jun 28) reworked the Instagram extractor with GraphQL endpoint + browser impersonation
- Uses yt-dlp directly (gallery-dl removed — was broken upstream with HTTP 400 on `/api/v1/media/{id}/info/`)
- Carousel posts should work: no `--no-playlist` is used in the download command

## Engine Routing
- YouTube → `yt-dlp` → `_handle_youtube()` (probe + quality picker → single file upload)
- Instagram → `"gallery-dl"` engine label → `_handle_instagram()` (direct queue → `download_instagram()` which now uses yt-dlp → single file or media group upload)

## Relevant Files
- `bot/services/downloader.py`: yt-dlp commands (`_base_cmd`, `probe_video`, `download_video`, `download_instagram`, `format_selector`) + cleanup
- `bot/handlers/video_download.py`: URL regex, engine selection, quality picker, direct queue for Instagram
- `bot/workers/download_worker.py`: Redis consumer, branches on engine → yt-dlp single-file (`_handle_youtube`) or Instagram multi-file (`_handle_instagram` for video/photo/carousel)
- `bot/handlers/cookie_login.py`: `/cookies` command for user cookie upload
- `bot.py`: main entry, handler registration, main menu, mode switching
- `scripts/refresh_cookies.py`: Chrome cookie decryption + refresh
- `/root/yt_cookies.txt`: working cookies file

## Commands
- `systemctl restart ebook-bot.service` — restart bot
- `systemctl restart ebook-download.service` — restart download worker
- `systemctl restart telegram-bot-api.service` — restart Bot API server
