# Changelog

All notable changes to this project, in [Keep a Changelog](https://keepachangelog.com/) format.

## [Unreleased]
### Added
- TikTok/Facebook/LinkedIn/Snapchat domain routing via yt-dlp
- `/fetch` command with SSRF-safe HTTP client (aiohttp + trafilatura)
- 78 new tests across 4 test files (143 total, all passing)
- `ebook-fetch.service` systemd unit for the fetch worker
- Redis connection lifecycle management (managed `redis_conn` fixture)

### Fixed
- TikTok URL regex now matches `@username` in path
- Provider cooldown test isolation (clean zset between tests)
- Redis auth in test suite (uses `redis_url` fixture with password)

## [2026-07-08]
### Added
- Audiobook TTS via edge-tts with language-to-voice mapping
- Twitter/X support via yt-dlp (`_handle_twitter` worker)
- Gallery-dl removed in favor of yt-dlp for Instagram
- Batch glossary extraction pipeline

## [2026-07-07]
### Added
- PDF ebook translator with PyMuPDF in-place edit (Mode A) and WeasyPrint regeneration (Mode B)
- Redis-backed provider pool rotation with cooldown tracking
- Full test suite (65 tests at time of initial commit)

### Added
- Initial project scaffold: bot, workers, ingest/translate/reassemble pipeline
