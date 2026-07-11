from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from ebook_translator.config import settings as get_settings
from ebook_translator.queue import connect

from bot.services.downloader import probe_video

cfg = get_settings()
log = logging.getLogger("download_handler")

URL_PATTERN = re.compile(
    r"(https?://)?"
    r"(www\.)?"
    r"(youtube\.com|youtu\.be|instagram\.com|twitter\.com|x\.com|"
    r"facebook\.com|fb\.watch|tiktok\.com|linkedin\.com|snapchat\.com)"
    r"/(watch\?v=|embed/|v/|shorts/|reel/|p/|tv/|stories/|[\w-]+/status/|[\w-]+/video/|[\w-]+/reel/|[\w-]+/photo/|[\w-]+)?"
    r"[\w&?=%-]+",
    re.IGNORECASE,
)

SUPPORTED_DOMAINS = {
    "youtube.com", "youtu.be",
    "instagram.com",
    "twitter.com", "x.com",
    "facebook.com", "fb.watch",
    "tiktok.com",
    "linkedin.com",
    "snapchat.com",
}

FACEBOOK_DOMAINS = {"facebook.com", "fb.watch"}
TIKTOK_DOMAINS = {"tiktok.com"}
LINKEDIN_DOMAINS = {"linkedin.com"}
SNAPCHAT_DOMAINS = {"snapchat.com"}


def get_domain_engine(domain: str) -> str:
    if domain in ("youtube.com", "youtu.be"):
        return "yt-dlp"
    if domain in ("instagram.com",):
        return "gallery-dl"
    if domain in ("twitter.com", "x.com"):
        return "twitter"
    if domain in FACEBOOK_DOMAINS | TIKTOK_DOMAINS | LINKEDIN_DOMAINS | SNAPCHAT_DOMAINS:
        return "yt-dlp"
    return "unsupported"


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    match = URL_PATTERN.search(text)
    if not match:
        if update.message.text and update.message.text.startswith("/download"):
            await update.message.reply_text(
                "Usage: `/download <YouTube or Instagram URL>`\n"
                "Or just paste a supported link directly."
            )
        return

    url = match.group(0)
    if not url.startswith("http"):
        url = "https://" + url

    domain = urlparse(url).netloc.replace("www.", "")
    if domain not in SUPPORTED_DOMAINS:
        await update.message.reply_text(
            "This link is not supported yet. Supported: YouTube, Instagram, Twitter/X, "
            "Facebook, TikTok, LinkedIn, Snapchat."
        )
        return

    # cooldown check
    user_id = str(update.effective_user.id)
    last_job = context.user_data.get("last_download", 0)
    cooldown = 20
    remaining = cooldown - (update.message.date.timestamp() - last_job) if last_job else 0
    if remaining > 0:
        await update.message.reply_text(
            f"Please wait {int(remaining)} seconds before starting another download."
        )
        return

    engine = get_domain_engine(domain)

    if engine in ("gallery-dl", "twitter"):
        # Instagram/Twitter: skip probe/quality picker, enqueue directly
        label = "Instagram" if engine == "gallery-dl" else "Twitter/X"
        msg = await update.message.reply_text(f"⏳ Queuing {label} download...")
        r = await connect(cfg.redis_url)
        await r.xadd(
            "download:pending",
            {
                "chat_id": str(update.effective_chat.id),
                "user_id": user_id,
                "url": url,
                "quality": "best",
                "engine": engine,
            },
        )
        from time import time
        context.user_data["last_download"] = int(time())
        await msg.edit_text("✅ Download queued. You'll receive the file shortly.")
        return

    # YouTube: probe and show quality picker (existing flow)
    msg = await update.message.reply_text("⏳ Fetching video info...")

    info = await probe_video(url)
    if info is None:
        await msg.edit_text(
            "Could not fetch info for this link. It may be private, deleted, "
            "age-restricted, or geo-blocked."
        )
        return

    title = info.get("title", "Untitled")
    duration = info.get("duration", 0)
    mins, secs = divmod(duration, 60)
    duration_str = f"{mins}:{secs:02d}" if duration else "unknown"

    formats = info.get("formats", [])
    if not formats:
        await msg.edit_text("No downloadable formats found for this video.")
        return

    context.user_data["download_url"] = url
    context.user_data["download_info"] = info
    context.user_data["download_engine"] = engine

    buttons = []

    def is_video_format(f: dict) -> bool:
        vcodec = f.get("vcodec") or ""
        return (
            (vcodec and vcodec != "none")
            or (f.get("height") or 0) > 0
            or f.get("resolution") not in (None, "", "audio-only")
        )

    def is_audio_format(f: dict) -> bool:
        acodec = f.get("acodec") or ""
        return acodec and acodec != "none"

    has_video = any(is_video_format(f) for f in formats)
    has_audio = any(is_audio_format(f) for f in formats)

    # Best quality (under size limit)
    buttons.append(InlineKeyboardButton("🎬 Best quality", callback_data="dl:best"))

    # Common resolution options
    seen_res = set()
    for target_height in [2160, 1440, 1080, 720, 480, 360]:
        match = [
            f
            for f in formats
            if (f.get("height") or 0) == target_height
            and is_video_format(f)
        ]
        if match and target_height not in seen_res:
            label = f"{target_height}p" if target_height >= 360 else f"{target_height}p"
            buttons.append(InlineKeyboardButton(label, callback_data=f"dl:{target_height}p"))
            seen_res.add(target_height)

    if has_audio:
        buttons.append(InlineKeyboardButton("🎵 Audio 64kbps", callback_data="dl:audio:64k"))
        buttons.append(InlineKeyboardButton("🎵 Audio 128kbps", callback_data="dl:audio:128k"))
        buttons.append(InlineKeyboardButton("🎵 Audio best (mp3)", callback_data="dl:audio:best"))

    await msg.edit_text(
        f"📹 *{title}*\n⏱ {duration_str}\n\nSelect quality:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([buttons]),
    )


async def handle_dl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data.removeprefix("dl:")
    url = context.user_data.get("download_url")
    if not url:
        await query.edit_message_text("Session expired — please send the link again.")
        return

    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id

    await query.edit_message_text("⏳ Queuing download...")

    engine = context.user_data.get("download_engine", "yt-dlp")

    r = await connect(cfg.redis_url)
    await r.xadd(
        "download:pending",
        {
            "chat_id": str(chat_id),
            "user_id": user_id,
            "url": url,
            "quality": data,
            "engine": engine,
        },
    )

    # update cooldown
    from time import time
    context.user_data["last_download"] = int(time())

    await query.edit_message_text(
        "✅ Download queued. You'll receive the file shortly."
    )


HANDLERS = [
    CommandHandler("download", handle_url),
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url),
    CallbackQueryHandler(handle_dl_callback, pattern="^dl:"),
]
