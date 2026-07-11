from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    redis_url: str
    base_dir: Path
    db_path: Path
    providers_path: Path
    log_dir: Path
    font_dir: Path
    telegram_api_base_url: str = "https://api.telegram.org"
    telegram_api_file_base_url: str = "https://api.telegram.org"
    download_tmp_dir: Path = Path("/tmp/tgbot_downloads")
    max_upload_mb: int = 1900
    per_user_cooldown_seconds: int = 60
    max_concurrent_downloads: int = 3
    cookies_file: str = ""
    bot_poll_interval: int = 5

    @property
    def jobs_dir(self) -> Path:
        return self.base_dir / "jobs"


def settings() -> Settings:
    base_dir = Path(os.getenv("BASE_DIR", "/var/www/ebook-translator"))
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        base_dir=base_dir,
        db_path=Path(os.getenv("DB_PATH", str(base_dir / "db.sqlite3"))),
        providers_path=Path(os.getenv("PROVIDERS_PATH", str(base_dir / "providers.yaml"))),
        log_dir=Path(os.getenv("LOG_DIR", "/var/log/ebook-translator")),
        font_dir=Path(os.getenv("FONT_DIR", str(base_dir / "fonts"))),
        telegram_api_base_url=os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org"),
        telegram_api_file_base_url=os.getenv("TELEGRAM_API_FILE_BASE_URL", "https://api.telegram.org"),
        download_tmp_dir=Path(os.getenv("DOWNLOAD_TMP_DIR", "/tmp/tgbot_downloads")),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "1900")),
        per_user_cooldown_seconds=int(os.getenv("PER_USER_COOLDOWN_SECONDS", "60")),
        max_concurrent_downloads=int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3")),
        cookies_file=os.getenv("COOKIES_FILE", ""),
        bot_poll_interval=int(os.getenv("BOT_POLL_INTERVAL", "5")),
    )


STREAM_INGEST = "ingest:pending"
STREAM_TRANSLATE = "translate:pending"
STREAM_REASSEMBLE = "reassemble:pending"
GROUP_INGEST = "ebook-ingest"
GROUP_TRANSLATE = "ebook-translate"
GROUP_REASSEMBLE = "ebook-reassemble"
COOLDOWN_ZSET = "provider:cooldowns"

