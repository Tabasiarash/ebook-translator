from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

log = logging.getLogger("cookie_handler")

COOKIES_DIR = Path("/root/yt_cookies_uploads")
COOKIES_DIR.mkdir(parents=True, exist_ok=True)


async def cmd_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "To upload your own YouTube cookies:\n\n"
        "1. Export your cookies from your browser as a Netscape-format cookies.txt file\n"
        "2. Send the .txt file to this bot\n\n"
        "Your cookies will replace the current cookie file used for downloads.\n"
        "You can also export cookies from a Chrome extension like \"Get cookies.txt LOCALLY\".\n\n"
        "Only .txt files are accepted."
    )


async def handle_cookie_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    if not doc or not doc.file_name or not doc.file_name.endswith(".txt"):
        await update.message.reply_text("Please send a .txt file (Netscape cookie format).")
        return

    user_id = str(update.effective_user.id)
    dest = COOKIES_DIR / f"user_{user_id}.txt"

    file = await doc.get_file()
    await file.download_to_drive(dest)

    # Validate: check it looks like a cookie file
    content = dest.read_text(errors="replace")
    if "# Netscape HTTP Cookie File" not in content and "youtube.com" not in content:
        dest.unlink(missing_ok=True)
        await update.message.reply_text(
            "This doesn't look like a valid cookies.txt file. "
            "Make sure it contains YouTube cookies in Netscape format."
        )
        return

    # Update env and point COOKIES_FILE to this user's file
    env_path = Path("/root/ebook-translator/.env")
    env_content = env_path.read_text()
    new_cookies = f"COOKIES_FILE={dest}"
    if "COOKIES_FILE=" in env_content:
        lines = env_content.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("COOKIES_FILE="):
                lines[i] = new_cookies
                break
        env_content = "\n".join(lines) + "\n"
    else:
        env_content += new_cookies + "\n"
    env_path.write_text(env_content)

    log.info("user %s uploaded cookies -> %s (%d bytes)", user_id, dest, dest.stat().st_size)
    await update.message.reply_text(
        "Cookies saved and activated. YouTube downloads should now work with your cookies."
    )


HANDLERS = [
    CommandHandler("cookies", cmd_cookies),
    MessageHandler(filters.Document.FileExtension("txt"), handle_cookie_file),
]
