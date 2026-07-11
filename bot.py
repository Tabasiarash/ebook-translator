from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from pathlib import Path

import fitz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from ebook_translator import db
from ebook_translator.config import STREAM_INGEST, STREAM_TRANSLATE, settings
from ebook_translator.eta import estimate_eta
from ebook_translator.languages import COMMON_LANGUAGES
from ebook_translator.logging_config import configure_logging
from ebook_translator.queue import connect
from ebook_translator.render_mode_review import render_review
from ebook_translator.tts import generate_audiobook

from bot.handlers.video_download import HANDLERS as DL_HANDLERS
from bot.handlers.cookie_login import HANDLERS as COOKIE_HANDLERS


cfg = settings()
log = configure_logging("ebook-bot", cfg.log_dir)


MAIN_MENU_BUTTON = [InlineKeyboardButton("🏠 Main Menu", callback_data="menu:main")]


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 PDF Translator", callback_data="mode:translator")],
        [InlineKeyboardButton("📹 Media Downloader", callback_data="mode:downloader")],
        [InlineKeyboardButton("📋 System Architecture", callback_data="action_arch")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🏠 *Main Menu*\n\nChoose a tool:",
        parse_mode="Markdown",
        reply_markup=main_menu_markup(),
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🏠 *Main Menu*\n\nChoose a tool:",
        parse_mode="Markdown",
        reply_markup=main_menu_markup(),
    )


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu:main":
        await query.edit_message_text(
            "🏠 *Main Menu*\n\nChoose a tool:",
            parse_mode="Markdown",
            reply_markup=main_menu_markup(),
        )
        return

    if data.startswith("mode:"):
        mode = data.removeprefix("mode:")
        context.user_data["mode"] = mode
        if mode == "translator":
            await query.edit_message_text(
                "📖 *PDF Translator*\n\nSend me a PDF file and I'll translate it.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([MAIN_MENU_BUTTON]),
            )
        elif mode == "downloader":
            await query.edit_message_text(
                "📹 *Media Downloader*\n\nSend me a YouTube or Instagram link to download.\n\n"
                "Supported: YouTube, Instagram reels/posts/stories",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([MAIN_MENU_BUTTON]),
            )


async def handle_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "action_translate":
        context.user_data["mode"] = "translator"
        await query.edit_message_text("Send me a PDF file to translate.", reply_markup=InlineKeyboardMarkup([MAIN_MENU_BUTTON]))
    elif query.data == "action_arch":
        await query.edit_message_text(_arch_text(), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([MAIN_MENU_BUTTON]))


async def handle_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    job_id = query.data.removeprefix("review:")
    job = await db.fetchone(cfg.db_path, "SELECT * FROM jobs WHERE job_id=?", (job_id,))
    if not job:
        await query.edit_message_text("Job not found.")
        return
    await query.edit_message_text("Generating review PDF...")
    try:
        out = await render_review(cfg.jobs_dir / job_id, job_id, job["target_lang"], cfg.font_dir, cfg.db_path)
        await context.bot.send_document(job["chat_id"], open(out, "rb"), filename=f"review_{job_id}_{job['target_lang']}.pdf", caption=f"Review copy for `{job_id}` ({job['target_lang']}).")
    except FileNotFoundError:
        await query.edit_message_text("Source files not found for this job.")
    except Exception as exc:
        await query.edit_message_text(f"Failed to generate review: {exc}")


async def receive_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    if not doc or doc.mime_type != "application/pdf":
        await update.message.reply_text("Please send a PDF file.")
        return
    mode = context.user_data.get("mode")
    if mode == "downloader":
        await update.message.reply_text("Switch to 📖 PDF Translator from the menu to translate PDFs.", reply_markup=InlineKeyboardMarkup([MAIN_MENU_BUTTON]))
        return
    job_id = uuid.uuid4().hex[:16]
    job_dir = cfg.jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    source_path = job_dir / "source.pdf"
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(source_path)
    words = quick_word_count(source_path)
    context.user_data["pending_pdf"] = {"job_id": job_id, "source_path": str(source_path), "words": words}
    buttons = [[InlineKeyboardButton(lang, callback_data=f"lang:{lang}")] for lang in COMMON_LANGUAGES]
    buttons.append([InlineKeyboardButton("Other (type it)", callback_data="lang_other")])
    await update.message.reply_text("Choose the target language:", reply_markup=InlineKeyboardMarkup(buttons))


async def choose_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "lang_other":
        context.user_data["awaiting_language"] = True
        await query.edit_message_text("Type the target language name.")
        return
    await create_translation_job(query.message.chat_id, query.from_user.id, query.data.removeprefix("lang:"), context, query)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mode = context.user_data.get("mode")

    if mode == "translator":
        if context.user_data.get("awaiting_language"):
            context.user_data["awaiting_language"] = False
            await create_translation_job(update.message.chat_id, update.effective_user.id, update.message.text.strip(), context)
        else:
            await update.message.reply_text("Send a PDF file to translate.", reply_markup=InlineKeyboardMarkup([MAIN_MENU_BUTTON]))
        return

    if mode == "downloader":
        for h in DL_HANDLERS:
            if isinstance(h, MessageHandler) and h.check_update(update):
                await h.callback(update, context)
                return
        await update.message.reply_text("Send a YouTube or Instagram link.", reply_markup=InlineKeyboardMarkup([MAIN_MENU_BUTTON]))
        return

    # No mode set: try everything (backwards compat)
    if context.user_data.get("awaiting_language"):
        context.user_data["awaiting_language"] = False
        await create_translation_job(update.message.chat_id, update.effective_user.id, update.message.text.strip(), context)
        return
    for h in DL_HANDLERS:
        if isinstance(h, MessageHandler) and h.check_update(update):
            await h.callback(update, context)
            return
    for h in COOKIE_HANDLERS:
        if isinstance(h, MessageHandler) and h.check_update(update):
            await h.callback(update, context)
            return


async def create_translation_job(chat_id: int, user_id: int, language: str, context: ContextTypes.DEFAULT_TYPE, query=None) -> None:
    pending = context.user_data.get("pending_pdf")
    if not pending:
        text = "Please send a PDF first."
        if query:
            await query.edit_message_text(text)
        else:
            await context.bot.send_message(chat_id, text)
        return
    await db.create_job(
        cfg.db_path,
        {
            "job_id": pending["job_id"],
            "user_id": str(user_id),
            "chat_id": chat_id,
            "target_lang": language,
            "source_lang": "English",
        },
    )
    r = await connect(cfg.redis_url)
    await r.xadd(STREAM_INGEST, {"job_id": pending["job_id"]})
    _, eta_text = await estimate_eta(r, cfg.providers_path, pending["words"])
    msg = f"Job `{pending['job_id']}` queued for {language}. Estimated time: {eta_text}."
    if query:
        await query.edit_message_text(msg, parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id, msg, parse_mode="Markdown")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        rows = await db.fetchall(
            cfg.db_path,
            "SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
            (str(update.effective_user.id),),
        )
    else:
        rows = [await db.fetchone(cfg.db_path, "SELECT * FROM jobs WHERE job_id=?", (context.args[0],))]
    lines = []
    for job in [row for row in rows if row]:
        total = max(int(job["total_chunks"] or 0), 1)
        done_row = await db.fetchone(
            cfg.db_path, "SELECT COUNT(*) as c FROM chunks WHERE job_id=? AND status='done'", (job["job_id"],)
        )
        done = done_row["c"] if done_row else 0
        pct = int((done / total) * 100) if total else 0
        lines.append(f"{job['job_id']}: {job['status']} / {pct}%")
    await update.message.reply_text("\n".join(lines) if lines else "No jobs found.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /cancel <job_id>")
        return
    job_id = context.args[0]
    job = await db.fetchone(cfg.db_path, "SELECT * FROM jobs WHERE job_id=? AND user_id=?", (job_id, str(update.effective_user.id)))
    if not job:
        await update.message.reply_text("Job not found.")
        return
    await db.update_job(cfg.db_path, job_id, status="cancelled")
    shutil.rmtree(cfg.jobs_dir / job_id, ignore_errors=True)
    await update.message.reply_text(f"Cancelled {job_id}.")


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resume half-finished or failed translation jobs.

    /resume                — list incomplete jobs for this user
    /resume <job_id>       — resume a specific job
    /resume all            — resume all incomplete jobs for this user
    """
    user_id = str(update.effective_user.id)

    if not context.args:
        rows = await db.fetchall(
            cfg.db_path,
            "SELECT * FROM jobs WHERE user_id=? AND status IN ('translating','reassembling','failed','ingesting') ORDER BY created_at DESC",
            (user_id,),
        )
        if not rows:
            await update.message.reply_text("No incomplete jobs found.")
            return
        lines = []
        for job in rows:
            total = max(int(job["total_chunks"] or 0), 1)
            done_row = await db.fetchone(
                cfg.db_path, "SELECT COUNT(*) as c FROM chunks WHERE job_id=? AND status='done'", (job["job_id"],)
            )
            done = done_row["c"] if done_row else 0
            failed_row = await db.fetchone(
                cfg.db_path, "SELECT COUNT(*) as c FROM chunks WHERE job_id=? AND status='failed'", (job["job_id"],)
            )
            failed = failed_row["c"] if failed_row else 0
            pct = int((done / total) * 100) if total else 0
            lines.append(f"`{job['job_id']}` — {job['target_lang']} — {job['status']} — {pct}% ({done}/{total} done, {failed} failed)")
        await update.message.reply_text(
            "Incomplete jobs:\n" + "\n".join(lines)
            + "\n\nUse `/resume <job_id>` to resume a job, or `/resume all` to resume all."
        )
        return

    job_ids = []
    if context.args[0] == "all":
        rows = await db.fetchall(
            cfg.db_path,
            "SELECT job_id FROM jobs WHERE user_id=? AND status IN ('translating','reassembling','failed','ingesting')",
            (user_id,),
        )
        job_ids = [r["job_id"] for r in rows]
    else:
        job_ids = context.args[:2]

    if not job_ids:
        await update.message.reply_text("No jobs to resume.")
        return

    resumed = []
    for jid in job_ids:
        job = await db.fetchone(
            cfg.db_path, "SELECT * FROM jobs WHERE job_id=? AND user_id=?", (jid, user_id)
        )
        if not job:
            await update.message.reply_text(f"Job `{jid}` not found.")
            continue
        if job["status"] == "done":
            await update.message.reply_text(f"Job `{jid}` is already done.")
            continue

        # Reset failed chunks to pending
        await db.execute(
            cfg.db_path,
            "UPDATE chunks SET status='pending' WHERE job_id=? AND status='failed'",
            (jid,),
        )

        # Re-queue pending chunks into the translate stream
        pending = await db.fetchall(
            cfg.db_path,
            "SELECT chunk_id, source_text FROM chunks WHERE job_id=? AND status='pending'",
            (jid,),
        )
        r = await connect(cfg.redis_url)
        for chunk in pending:
            await r.xadd(
                STREAM_TRANSLATE,
                {
                    "job_id": jid,
                    "chunk_id": str(chunk["chunk_id"]),
                    "text": chunk["source_text"],
                },
            )

        await db.update_job(cfg.db_path, jid, status="translating")
        resumed.append(f"`{jid}` ({len(pending)} chunks re-queued)")

    await update.message.reply_text(
        "Resumed:\n" + "\n".join(resumed) if resumed else "Nothing was resumed."
    )


async def done_listener(app: Application) -> None:
    try:
        r = await connect(cfg.redis_url)
        pubsub = r.pubsub()
        await pubsub.psubscribe("job:done:*")
        while True:
            msg = await pubsub.get_message(timeout=5.0)
            if msg is None:
                continue
            if msg.get("type") != "pmessage":
                continue
            job_id = msg["data"]
            job = await db.fetchone(cfg.db_path, "SELECT * FROM jobs WHERE job_id=?", (job_id,))
            if not job:
                continue
            elapsed = int((job["completed_at"] or time.time()) - job["created_at"])
            report = (
                f"Translated {job['total_pages']} pages, {job['total_chunks']} chunks in {elapsed // 60} minutes."
            )
            buttons = [[InlineKeyboardButton("Review copy", callback_data=f"review:{job_id}")]]
            await app.bot.send_document(job["chat_id"], cfg.jobs_dir / job_id / f"translated_{job['target_lang']}.pdf", caption=report, reply_markup=InlineKeyboardMarkup(buttons))
    except (RuntimeError, asyncio.CancelledError):
        pass


async def periodic_pings(app: Application) -> None:
    while True:
        await asyncio.sleep(21600)
        jobs = await db.fetchall(
            cfg.db_path,
            "SELECT * FROM jobs WHERE status IN ('translating','reassembling') AND created_at < ?",
            (int(time.time()) - 3600,),
        )
        for job in jobs:
            done_row = await db.fetchone(
                cfg.db_path, "SELECT COUNT(*) as c FROM chunks WHERE job_id=? AND status='done'", (job["job_id"],)
            )
            done = done_row["c"] if done_row else 0
            elapsed = int(time.time() - job["created_at"])
            msg = f"Still working on `{job['job_id']}` ({job['target_lang']}): {done}/{job['total_chunks']} chunks done, {elapsed // 60} minutes elapsed."
            try:
                await app.bot.send_message(job["chat_id"], msg, parse_mode="Markdown")
            except Exception:
                pass


async def progress_reporter(app: Application) -> None:
    milestones: dict[str, set[int]] = {}
    while True:
        await asyncio.sleep(15)
        try:
            jobs = await db.fetchall(
                cfg.db_path,
                "SELECT * FROM jobs WHERE status IN ('translating','reassembling')",
            )
        except Exception:
            continue
        for job in jobs:
            total = int(job.get("total_chunks") or 0)
            if total <= 0:
                continue
            try:
                done_row = await db.fetchone(
                    cfg.db_path, "SELECT COUNT(*) as c FROM chunks WHERE job_id=? AND status='done'", (job["job_id"],)
                )
            except Exception:
                continue
            done = done_row["c"] if done_row else 0
            pct = int((done / total) * 100)
            if jid := job["job_id"]:
                done_set = milestones.setdefault(jid, set())
                for m in (30, 60, 80):
                    if pct >= m and m not in done_set:
                        done_set.add(m)
                        msg = f"Translation `{jid}` to {job['target_lang']}: {pct}% ({done}/{total})"
                        try:
                            await app.bot.send_message(job["chat_id"], msg, parse_mode="Markdown")
                        except Exception:
                            pass


async def post_init(app: Application) -> None:
    await db.init_db(cfg.db_path)
    cfg.jobs_dir.mkdir(parents=True, exist_ok=True)
    app.create_task(done_listener(app))
    app.create_task(periodic_pings(app))
    app.create_task(progress_reporter(app))


def quick_word_count(path: Path) -> int:
    try:
        doc = fitz.open(path)
        words = sum(len(page.get_text("text").split()) for page in doc)
        doc.close()
        return words
    except Exception:
        return 250


def _arch_text() -> str:
    return (
        "📚 *Ebook Translator — Architecture*\n\n"
        "*Pipeline*\n"
        "`1. ingest_worker` — PDF extraction (PyMuPDF), glossary extraction via LLM\n"
        "`2. translate_worker` — per-chunk translation with glossary injection\n"
        "`3. reassemble_worker` — PDF generation (Mode A reinsertion / Mode B WeasyPrint)\n"
        "`4. reconcile_worker` — periodic stuck-job recovery\n\n"
        "*Rendering Modes*\n"
        "• `Mode A` — reinsert translated text into original PDF layout\n"
        "• `Mode B` — full HTML→PDF regeneration via WeasyPrint (RTL/CJK)\n"
        "• `A_with_B_fallback` — Mode A first, falls back to Mode B on overflow\n\n"
        "*Languages* "
        "(19): Farsi, Arabic, Urdu, Hebrew, Chinese, Japanese, Korean, Hindi,\n"
        "Spanish, French, German, Portuguese, Italian, Russian, Turkish\n\n"
        "*Providers*: Gemini (prio 1) → Groq (prio 2) → OpenRouter (prio 3)\n"
        "*Storage*: SQLite (jobs/chunks/glossary) + Redis Streams (queue)\n"
        "*Fonts*: Noto Sans (Latin/Cyrillic), Vazirmatn (Farsi), NotoSansArabic,\n"
        "NotoNastaliqUrdu, NotoSansCJK, NotoSansDevanagari, NotoSansHebrew"
    )


async def arch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_arch_text(), parse_mode="Markdown")


async def review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /review <job_id>")
        return
    job_id = context.args[0]
    job = await db.fetchone(cfg.db_path, "SELECT * FROM jobs WHERE job_id=?", (job_id,))
    if not job:
        await update.message.reply_text("Job not found.")
        return
    await update.message.reply_text("Generating review PDF...")
    try:
        out = await render_review(cfg.jobs_dir / job_id, job_id, job["target_lang"], cfg.font_dir, cfg.db_path)
        with open(out, "rb") as f:
            await update.message.reply_document(f, filename=f"review_{job_id}_{job['target_lang']}.pdf", caption=f"Review PDF for `{job_id}` ({job['target_lang']}). Original above, translation below.")
    except FileNotFoundError:
        await update.message.reply_text("Source files not found for this job.")
    except Exception as exc:
        await update.message.reply_text(f"Failed to generate review: {exc}")


async def audiobook(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        rows = await db.fetchall(
            cfg.db_path,
            "SELECT job_id, target_lang FROM jobs WHERE user_id=? AND status='done' ORDER BY created_at DESC LIMIT 10",
            (str(update.effective_user.id),),
        )
        if not rows:
            await update.message.reply_text("No completed jobs found. Use `/audiobook <job_id>` to generate an audiobook for a done translation.")
            return
        lines = [f"`{r['job_id']}` — {r['target_lang']}" for r in rows]
        await update.message.reply_text(
            "Completed jobs:\n" + "\n".join(lines) + "\n\nUse `/audiobook <job_id>` to generate narration.",
            parse_mode="Markdown",
        )
        return

    job_id = context.args[0]
    job = await db.fetchone(cfg.db_path, "SELECT * FROM jobs WHERE job_id=?", (job_id,))
    if not job:
        await update.message.reply_text("Job not found.")
        return
    if job["status"] != "done":
        await update.message.reply_text(f"Job `{job_id}` is not done yet (status: {job['status']}).")
        return
    msg = await update.message.reply_text(f"Generating audiobook for `{job_id}` ({job['target_lang']})...")
    try:
        result = await generate_audiobook(
            cfg.jobs_dir / job_id,
            job_id,
            job["target_lang"],
            cfg.db_path,
        )
        if "error" in result:
            await msg.edit_text(f"Failed: {result['error']}")
            return
        duration = result.get("duration_estimate", 0)
        caption = f"Audiobook for `{job_id}` ({job['target_lang']}), {result['segments']} segments, ~{duration}min."
        with open(result["path"], "rb") as f:
            await context.bot.send_audio(job["chat_id"], f, caption=caption, parse_mode="Markdown")
        await msg.edit_text(f"Audiobook generated for `{job_id}`.")
    except Exception as exc:
        await msg.edit_text(f"Failed to generate audiobook: {exc}")


def main() -> None:
    if not cfg.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    app = (
        Application.builder()
        .token(cfg.telegram_bot_token)
        .base_url(cfg.telegram_api_base_url + "/bot")
        .base_file_url(cfg.telegram_api_file_base_url + "/file/bot")
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("arch", arch))
    app.add_handler(CommandHandler("review", review))
    app.add_handler(CommandHandler("audiobook", audiobook))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(CallbackQueryHandler(handle_menu, pattern="^(menu:|mode:)"))
    app.add_handler(CallbackQueryHandler(handle_action, pattern="^action_"))
    app.add_handler(CallbackQueryHandler(handle_review_callback, pattern="^review:"))
    app.add_handler(CallbackQueryHandler(choose_language, pattern="^lang:"))
    app.add_handler(CallbackQueryHandler(choose_language, pattern="^lang_other$"))
    app.add_handler(MessageHandler(filters.Document.PDF, receive_pdf))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    for h in DL_HANDLERS:
        if isinstance(h, (CommandHandler, CallbackQueryHandler)):
            app.add_handler(h)
    for h in COOKIE_HANDLERS:
        if isinstance(h, (CommandHandler, CallbackQueryHandler)):
            app.add_handler(h)
    app.run_polling()


if __name__ == "__main__":
    main()
