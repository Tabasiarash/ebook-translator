from __future__ import annotations

import asyncio
import json
import socket
import time

from ebook_translator import db
from ebook_translator.config import GROUP_REASSEMBLE, STREAM_REASSEMBLE, settings
from ebook_translator.logging_config import configure_logging
from ebook_translator.queue import connect, ensure_group, read_group
from ebook_translator.render import render_final


async def main() -> None:
    cfg = settings()
    log = configure_logging("ebook-reassemble", cfg.log_dir)
    await db.init_db(cfg.db_path)
    r = await connect(cfg.redis_url)
    await ensure_group(r, STREAM_REASSEMBLE, GROUP_REASSEMBLE)
    consumer = socket.gethostname()
    log.info("reassemble worker started")
    while True:
        rows = await read_group(r, STREAM_REASSEMBLE, GROUP_REASSEMBLE, consumer)
        if not rows:
            continue
        for stream, messages in rows:
            for msg_id, data in messages:
                job_id = data["job_id"]
                try:
                    job = await db.fetchone(cfg.db_path, "SELECT * FROM jobs WHERE job_id=?", (job_id,))
                    if not job or job["status"] in {"cancelled", "done", "failed"}:
                        await r.xack(stream, GROUP_REASSEMBLE, msg_id)
                        continue

                    done_row = await db.fetchone(
                        cfg.db_path,
                        "SELECT COUNT(*) as c FROM chunks WHERE job_id=? AND status IN ('done','failed')",
                        (job_id,),
                    )
                    done = done_row["c"] if done_row else 0
                    total = int(job["total_chunks"] or 0)
                    if total <= 0 or done < total:
                        await r.xack(stream, GROUP_REASSEMBLE, msg_id)
                        continue

                    await db.update_job(cfg.db_path, job_id, status="reassembling")
                    chunks = await db.fetchall(
                        cfg.db_path,
                        "SELECT chunk_id,page_num,block_id,translated_text,source_text FROM chunks WHERE job_id=? ORDER BY chunk_id",
                        (job_id,),
                    )
                    translations = {
                        str(row["chunk_id"]): {
                            "chunk_id": int(row["chunk_id"]),
                            "page_id": row["page_num"],
                            "block_ids": row["block_id"].split(",") if row["block_id"] else [],
                            "translated_text": row["translated_text"] or row["source_text"],
                        }
                        for row in chunks
                    }
                    job_dir = cfg.jobs_dir / job_id
                    (job_dir / "translations.json").write_text(
                        json.dumps(translations, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    final_path, fallback_pages = render_final(job_dir, job["target_lang"], cfg.font_dir)
                    await db.update_job(
                        cfg.db_path,
                        job_id,
                        status="done",
                        completed_at=int(time.time()),
                    )
                    await r.publish(f"job:done:{job_id}", job_id)
                    await r.xack(stream, GROUP_REASSEMBLE, msg_id)
                    log.info(f"job={job_id} reassembled final={final_path}")
                except Exception as exc:
                    await db.update_job(cfg.db_path, job_id, status="failed")
                    log.exception(f"job={job_id} reassemble failed: {exc}")
                    await r.xack(stream, GROUP_REASSEMBLE, msg_id)


if __name__ == "__main__":
    asyncio.run(main())
