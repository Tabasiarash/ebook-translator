# Work State

## Objective
Telegram bot for media downloads (YouTube, Instagram, Twitter/X, Facebook, TikTok, LinkedIn, Snapchat) plus PDF ebook translation via local Bot API server.

## Architecture
- User sends URL → bot queues to Redis `download:pending` stream
- Download worker picks up, downloads via yt-dlp, uploads via local Bot API
- All services run via systemd on the same machine
- Separate `/fetch` command for generic URL relay with SSRF safeguards

## Key Facts
- Bot token: `8001452590:AAGEHtLFG5riMFxEeOwVN_plR9Uawiwc9TY`
- API IDs: `api_id=33282119`, `api_hash=c24cf53f30908e78cbaf76dd67214ac7`
- Bot API server: port 8082 at `telegram-bot-api.service`
- Cookies: `/root/yt_cookies.txt` decrypted from Chrome Profile 4
- Cookie refresh: `scripts/refresh_cookies.py` runs hourly via cron
- YouTube requires cookies + `--remote-components ejs:github --js-runtimes node` for JS challenge
- Worker auto-deletes files after upload (single `file_path.unlink()` for yt-dlp, `shutil.rmtree(job_dir)` for multi-file)

## Platform Status
| Platform | yt-dlp support | Probe | Notes |
|---|---|---|---|
| YouTube | Solid native | probe + quality picker | Requires cookies + js-runtimes for JS challenge |
| Instagram | Solid native (via yt-dlp) | skip probe, queue directly | Carousel posts supported via media_group |
| Twitter/X | Solid native | skip probe, queue directly | Single tweet only, no thread crawling |
| Facebook | Solid native | probe + quality picker | Public videos/reels, private needs cookies |
| TikTok | Solid native | probe + quality picker | May block datacenter IPs |
| LinkedIn | Weak/partial | probe + quality picker | Best-effort, "limited support" error on failure |
| Snapchat | Weak/partial | probe + quality picker | Best-effort, "limited support" error on failure |

## Engine Routing (in `get_domain_engine(domain)`)
- YouTube → `"yt-dlp"` → `_handle_youtube()` (probe + quality picker → single file upload)
- Instagram → `"gallery-dl"` → `_handle_instagram()` (direct queue → multi-file or media group)
- Twitter/X → `"twitter"` → `_handle_twitter()` (direct queue → multi-file or media group)
- Facebook, TikTok, LinkedIn, Snapchat → `"yt-dlp"` → `_handle_youtube()` (probe + quality picker → single file upload)
- LinkedIn/Snapchat failures → `_notify_fail(reason="best_effort")` → distinct "limited support" message

## Commands
- `/start` `/menu` — main menu navigation
- `/download` — download video from supported platforms
- `/fetch <url>` — generic URL relay with SSRF safeguards
- `/cancel` — cancel a download
- `/cookies` — upload cookies.txt
- `/audiobook` — generate TTS narration (ebook translator)
- `/status` `/review` `/resume` `/arch` — ebook translator utilities

## Services
- `systemctl restart ebook-bot.service` — restart bot
- `systemctl restart ebook-download.service` — restart download worker
- `systemctl restart telegram-bot-api.service` — restart Bot API server

## Relevant Files
- `bot/handlers/video_download.py`: URL regex, `get_domain_engine()`, quality picker, direct queue for Instagram/Twitter
- `bot/handlers/fetch.py`: `/fetch` command with SSRF safeguards (IP block, size cap, rate limiting)
- `bot/handlers/cookie_login.py`: `/cookies` command for user cookie upload
- `bot/services/downloader.py`: yt-dlp commands, `download_video`, `download_instagram`, `download_twitter`
- `bot/workers/download_worker.py`: Redis consumer, `_handle_youtube`, `_handle_instagram`, `_handle_twitter`
- `bot/workers/fetch_worker.py`: Redis consumer for fetch:pending stream, trafilatura HTML extraction
- `bot.py`: main entry, handler registration, main menu, mode switching
