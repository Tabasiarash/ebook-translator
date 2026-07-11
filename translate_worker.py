from __future__ import annotations

import asyncio
import socket

from ebook_translator import db
from ebook_translator.config import GROUP_TRANSLATE, STREAM_REASSEMBLE, STREAM_TRANSLATE, settings
from ebook_translator.logging_config import configure_logging
from ebook_translator.providers import (
    RateLimited,
    load_provider_keys_async,
    mark_cooldown,
    pick_available,
    sleep_until_next_available,
    translate_text,
)
from ebook_translator.queue import connect, ensure_group, read_group


async def requeue_stuck_chunks(r, db_path, log) -> None:
    rows = await db.fetchall(
        db_path,
        "SELECT job_id, chunk_id FROM chunks WHERE status='translating'",
    )
    for row in rows:
        job = await db.fetchone(db_path, "SELECT status FROM jobs WHERE job_id=?", (row["job_id"],))
        if job and job["status"] not in ("cancelled", "done", "failed"):
            source = await db.fetchone(
                db_path,
                "SELECT source_text FROM chunks WHERE job_id=? AND chunk_id=?",
                (row["job_id"], row["chunk_id"]),
            )
            if source:
                await r.xadd(
                    STREAM_TRANSLATE,
                    {
                        "job_id": row["job_id"],
                        "chunk_id": str(row["chunk_id"]),
                        "text": source["source_text"],
                    },
                )
            await db.execute(
                db_path,
                "UPDATE chunks SET status='pending' WHERE job_id=? AND chunk_id=?",
                (row["job_id"], row["chunk_id"]),
            )
    if rows:
        log.info(f"requeued {len(rows)} stuck chunks")


async def get_glossary_context(db_path: str, job_id: str, source_text: str) -> str | None:
    terms = await db.fetchall(
        db_path,
        "SELECT source_term, target_term FROM glossary WHERE job_id=? AND target_term != ''",
        (job_id,),
    )
    matched = []
    for term in terms:
        if term["source_term"] in source_text:
            matched.append(f'- "{term["source_term"]}" -> "{term["target_term"]}"')
    if matched:
        return "\n".join(matched)
    return None


async def main() -> None:
    cfg = settings()
    log = configure_logging("ebook-translate", cfg.log_dir)
    await db.init_db(cfg.db_path)

    r = await connect(cfg.redis_url)
    await ensure_group(r, STREAM_TRANSLATE, GROUP_TRANSLATE)
    consumer = f"{socket.gethostname()}-{id(r)}"

    await requeue_stuck_chunks(r, cfg.db_path, log)
    log.info("translate worker started")

    while True:
        provider_keys = await load_provider_keys_async(cfg.providers_path)
        if not provider_keys:
            log.error("no provider keys configured; sleeping")
            await asyncio.sleep(60)
            continue
        rows = await read_group(r, STREAM_TRANSLATE, GROUP_TRANSLATE, consumer)
        if not rows:
            continue
        for stream, messages in rows:
            for msg_id, data in messages:
                job_id = data["job_id"]
                chunk_id = int(data["chunk_id"])
                job = await db.fetchone(cfg.db_path, "SELECT status, target_lang FROM jobs WHERE job_id=?", (job_id,))
                if not job or job["status"] == "cancelled":
                    await r.xack(stream, GROUP_TRANSLATE, msg_id)
                    continue

                await db.execute(
                    cfg.db_path,
                    "UPDATE chunks SET status='translating' WHERE job_id=? AND chunk_id=?",
                    (job_id, chunk_id),
                )

                attempts = 0
                while True:
                    key = await pick_available(r, provider_keys)
                    if not key:
                        await sleep_until_next_available(r)
                        continue
                    try:
                        glossary_ctx = await get_glossary_context(cfg.db_path, job_id, data["text"])
                        translated = await translate_text(key, job["target_lang"], data["text"], glossary_ctx)
                        await db.execute(
                            cfg.db_path,
                            "UPDATE chunks SET translated_text=?, status='done', provider_used=?, updated_at=? WHERE job_id=? AND chunk_id=?",
                            (translated, key.identity, int(asyncio.get_event_loop().time()), job_id, chunk_id),
                        )

                        await r.xadd(STREAM_REASSEMBLE, {"job_id": job_id})
                        await r.xack(stream, GROUP_TRANSLATE, msg_id)
                        break
                    except RateLimited as exc:
                        await mark_cooldown(r, key, exc.retry_after)
                        await r.xadd(STREAM_TRANSLATE, data)
                        await r.xack(stream, GROUP_TRANSLATE, msg_id)
                        log.warning(f"provider={key.identity} cooldown={exc.retry_after}s job={job_id}")
                        await db.execute(
                            cfg.db_path,
                            "UPDATE chunks SET status='pending' WHERE job_id=? AND chunk_id=?",
                            (job_id, chunk_id),
                        )
                        break
                    except Exception as exc:
                        await mark_cooldown(r, key, 300)
                        attempts += 1
                        if attempts < 3:
                            log.warning(f"provider={key.identity} failed; trying another model job={job_id}: {exc}")
                            continue
                        await db.execute(
                            cfg.db_path,
                            "UPDATE chunks SET status='failed', provider_used=?, updated_at=? WHERE job_id=? AND chunk_id=?",
                            (key.identity, int(asyncio.get_event_loop().time()), job_id, chunk_id),
                        )
                        await r.xadd(STREAM_REASSEMBLE, {"job_id": job_id})
                        await r.xack(stream, GROUP_TRANSLATE, msg_id)
                        log.exception(f"chunk failed job={job_id} chunk={chunk_id}: {exc}")
                        break


if __name__ == "__main__":
    asyncio.run(main())
