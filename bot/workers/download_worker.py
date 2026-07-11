from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import time

from telegram import Bot, InputMediaAudio, InputMediaPhoto, InputMediaVideo
from telegram.request import HTTPXRequest

from ebook_translator import db
from ebook_translator.config import settings
from ebook_translator.logging_config import configure_logging
from ebook_translator.queue import connect, ensure_group, read_group

from bot.services.downloader import DOWNLOAD_TMP_DIR, cleanup_orphans, download_instagram, download_twitter, download_video

STREAM_DOWNLOAD = "download:pending"
GROUP_DOWNLOAD = "ebook-download"

log = logging.getLogger("download_worker")


async def main() -> None:
    cfg = settings()
    log = configure_logging("ebook-download", cfg.log_dir)

    DOWNLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_orphans()
    log.info("download worker started")

    r = await connect(cfg.redis_url)
    await ensure_group(r, STREAM_DOWNLOAD, GROUP_DOWNLOAD)
    consumer = f"{socket.gethostname()}-download"

    while True:
        rows = await read_group(r, STREAM_DOWNLOAD, GROUP_DOWNLOAD, consumer)
        if not rows:
            await asyncio.sleep(1)
            continue

        for stream, messages in rows:
            for msg_id, data in messages:
                chat_id = int(data["chat_id"])
                user_id = data["user_id"]
                url = data["url"]
                quality = data.get("quality", "best")
                engine = data.get("engine", "yt-dlp")
                job_id = msg_id

                try:
                    if engine == "gallery-dl":
                        await _handle_instagram(cfg, r, stream, msg_id, chat_id, url, job_id)
                    elif engine == "twitter":
                        await _handle_twitter(cfg, r, stream, msg_id, chat_id, url, job_id)
                    else:
                        await _handle_youtube(cfg, r, stream, msg_id, chat_id, url, quality, job_id)
                except Exception as exc:
                    log.exception("download worker error: %s", exc)
                    await r.xack(stream, GROUP_DOWNLOAD, msg_id)
                    await _notify_fail(r, chat_id, url)


async def _handle_youtube(
    cfg, r, stream: str, msg_id: str, chat_id: int, url: str, quality: str, job_id: str
) -> None:
    """Download YouTube video via yt-dlp and upload."""
    file_path = await download_video(url, quality, job_id)
    if file_path is None:
        await r.xack(stream, GROUP_DOWNLOAD, msg_id)
        await _notify_fail(r, chat_id, url)
        return

    file_size = file_path.stat().st_size
    if file_size > 1900 * 1024 * 1024:
        file_path.unlink(missing_ok=True)
        await _notify_fail(r, chat_id, url, reason="too_large")
        await r.xack(stream, GROUP_DOWNLOAD, msg_id)
        return

    req = HTTPXRequest(connection_pool_size=1)
    bot = Bot(token=cfg.telegram_bot_token, base_url=cfg.telegram_api_base_url + "/bot", request=req)

    try:
        with open(file_path, "rb") as f:
            if quality.startswith("audio"):
                await bot.send_audio(chat_id=chat_id, audio=f, title=file_path.stem)
            else:
                await bot.send_video(chat_id=chat_id, video=f, supports_streaming=True)
        log.info("uploaded yt job=%s chat=%s size=%d", job_id, chat_id, file_size)
    except Exception as exc:
        log.error("upload failed yt job=%s: %s", job_id, exc)
        raise
    finally:
        file_path.unlink(missing_ok=True)

    await r.xack(stream, GROUP_DOWNLOAD, msg_id)


async def _handle_instagram(
    cfg, r, stream: str, msg_id: str, chat_id: int, url: str, job_id: str
) -> None:
    """Download Instagram content and upload as video/photo/media_group."""
    files = await download_instagram(url, job_id)
    job_dir = DOWNLOAD_TMP_DIR / job_id

    if files is None:
        # Check if it was an auth error vs generic failure
        await r.xack(stream, GROUP_DOWNLOAD, msg_id)
        await _notify_fail(r, chat_id, url)
        shutil.rmtree(job_dir, ignore_errors=True)
        return

    req = HTTPXRequest(connection_pool_size=1)
    bot = Bot(token=cfg.telegram_bot_token, base_url=cfg.telegram_api_base_url + "/bot", request=req)

    try:
        # Check sizes
        over_limit = False
        for f in files:
            if f.stat().st_size > 1900 * 1024 * 1024:
                over_limit = True
                break
        if over_limit:
            await _notify_fail(r, chat_id, url, reason="too_large")
            await r.xack(stream, GROUP_DOWNLOAD, msg_id)
            return

        video_extensions = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
        image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

        def _classify(path: Path) -> str:
            ext = path.suffix.lower()
            if ext in video_extensions:
                return "video"
            if ext in image_extensions:
                return "image"
            return "other"

        classified = [_classify(f) for f in files]

        if len(files) == 1:
            f = files[0]
            ftype = classified[0]
            with open(f, "rb") as fh:
                if ftype == "video":
                    await bot.send_video(chat_id=chat_id, video=fh, supports_streaming=True)
                elif ftype == "image":
                    await bot.send_photo(chat_id=chat_id, photo=fh)
                else:
                    await bot.send_document(chat_id=chat_id, document=fh)
            log.info("uploaded ig single job=%s chat=%s type=%s", job_id, chat_id, ftype)

        else:
            # Carousel: send as media group
            media_group = []
            for i, f in enumerate(files):
                ftype = classified[i]
                with open(f, "rb") as fh:
                    data = fh.read()
                if ftype == "video":
                    media_group.append(InputMediaVideo(data, filename=f.name))
                elif ftype == "image":
                    media_group.append(InputMediaPhoto(data))
                else:
                    continue
            if media_group:
                await bot.send_media_group(chat_id=chat_id, media=media_group)
                log.info("uploaded ig carousel job=%s chat=%s count=%d", job_id, chat_id, len(media_group))

    except Exception as exc:
        log.error("upload failed ig job=%s: %s", job_id, exc)
        raise
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
        log.info("deleted job dir: %s", job_id)

    await r.xack(stream, GROUP_DOWNLOAD, msg_id)


async def _handle_twitter(
    cfg, r, stream: str, msg_id: str, chat_id: int, url: str, job_id: str
) -> None:
    """Download Twitter/X content and upload as video/photo/media_group."""
    files = await download_twitter(url, job_id)
    job_dir = DOWNLOAD_TMP_DIR / job_id

    if files is None:
        await r.xack(stream, GROUP_DOWNLOAD, msg_id)
        await _notify_fail(r, chat_id, url)
        shutil.rmtree(job_dir, ignore_errors=True)
        return

    req = HTTPXRequest(connection_pool_size=1)
    bot = Bot(token=cfg.telegram_bot_token, base_url=cfg.telegram_api_base_url + "/bot", request=req)

    try:
        over_limit = False
        for f in files:
            if f.stat().st_size > 1900 * 1024 * 1024:
                over_limit = True
                break
        if over_limit:
            await _notify_fail(r, chat_id, url, reason="too_large")
            await r.xack(stream, GROUP_DOWNLOAD, msg_id)
            return

        video_extensions = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
        image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

        def _classify(path: Path) -> str:
            ext = path.suffix.lower()
            if ext in video_extensions:
                return "video"
            if ext in image_extensions:
                return "image"
            return "other"

        classified = [_classify(f) for f in files]

        if len(files) == 1:
            f = files[0]
            ftype = classified[0]
            with open(f, "rb") as fh:
                if ftype == "video":
                    await bot.send_video(chat_id=chat_id, video=fh, supports_streaming=True)
                elif ftype == "image":
                    await bot.send_photo(chat_id=chat_id, photo=fh)
                else:
                    await bot.send_document(chat_id=chat_id, document=fh)
            log.info("uploaded twitter single job=%s chat=%s type=%s", job_id, chat_id, ftype)
        else:
            media_group = []
            for i, f in enumerate(files):
                ftype = classified[i]
                with open(f, "rb") as fh:
                    data = fh.read()
                if ftype == "video":
                    media_group.append(InputMediaVideo(data, filename=f.name))
                elif ftype == "image":
                    media_group.append(InputMediaPhoto(data))
                else:
                    continue
            if media_group:
                await bot.send_media_group(chat_id=chat_id, media=media_group)
                log.info("uploaded twitter carousel job=%s chat=%s count=%d", job_id, chat_id, len(media_group))

    except Exception as exc:
        log.error("upload failed twitter job=%s: %s", job_id, exc)
        raise
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
        log.info("deleted job dir: %s", job_id)

    await r.xack(stream, GROUP_DOWNLOAD, msg_id)


async def _handle_twitter(
    cfg, r, stream: str, msg_id: str, chat_id: int, url: str, job_id: str
) -> None:
    """Download Twitter/X content and upload (reuses Instagram carousel logic)."""
    files = await download_twitter(url, job_id)
    job_dir = DOWNLOAD_TMP_DIR / job_id

    if files is None:
        await r.xack(stream, GROUP_DOWNLOAD, msg_id)
        await _notify_fail(r, chat_id, url)
        shutil.rmtree(job_dir, ignore_errors=True)
        return

    req = HTTPXRequest(connection_pool_size=1)
    bot = Bot(token=cfg.telegram_bot_token, base_url=cfg.telegram_api_base_url + "/bot", request=req)

    try:
        over_limit = False
        for f in files:
            if f.stat().st_size > 1900 * 1024 * 1024:
                over_limit = True
                break
        if over_limit:
            await _notify_fail(r, chat_id, url, reason="too_large")
            await r.xack(stream, GROUP_DOWNLOAD, msg_id)
            return

        video_extensions = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
        image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

        def _classify(path: Path) -> str:
            ext = path.suffix.lower()
            if ext in video_extensions:
                return "video"
            if ext in image_extensions:
                return "image"
            return "other"

        classified = [_classify(f) for f in files]

        if len(files) == 1:
            f = files[0]
            ftype = classified[0]
            with open(f, "rb") as fh:
                if ftype == "video":
                    await bot.send_video(chat_id=chat_id, video=fh, supports_streaming=True)
                elif ftype == "image":
                    await bot.send_photo(chat_id=chat_id, photo=fh)
                else:
                    await bot.send_document(chat_id=chat_id, document=fh)
            log.info("uploaded tw single job=%s chat=%s type=%s", job_id, chat_id, ftype)
        else:
            media_group = []
            for i, f in enumerate(files):
                ftype = classified[i]
                with open(f, "rb") as fh:
                    data = fh.read()
                if ftype == "video":
                    media_group.append(InputMediaVideo(data, filename=f.name))
                elif ftype == "image":
                    media_group.append(InputMediaPhoto(data))
                else:
                    continue
            if media_group:
                await bot.send_media_group(chat_id=chat_id, media=media_group)
                log.info("uploaded tw carousel job=%s chat=%s count=%d", job_id, chat_id, len(media_group))

    except Exception as exc:
        log.error("upload failed tw job=%s: %s", job_id, exc)
        raise
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)

    await r.xack(stream, GROUP_DOWNLOAD, msg_id)


async def _notify_fail(r, chat_id: int, url: str, reason: str = "") -> None:
    """Send failure notification via Redis pubsub."""
    messages = {
        "too_large": "The file exceeds the 1.9GB upload limit and could not be downloaded.",
        "auth": "Instagram login expired. Use /cookies to upload a fresh cookies.txt file.",
        "": "Download failed. The content may be private, deleted, or unavailable.",
    }
    msg = messages.get(reason, messages[""])
    await r.publish(f"download:fail:{chat_id}", f"{msg}\n{url}")


if __name__ == "__main__":
    asyncio.run(main())
