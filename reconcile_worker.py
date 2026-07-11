from __future__ import annotations

import asyncio
import time

from ebook_translator import db
from ebook_translator.config import settings
from ebook_translator.logging_config import configure_logging


async def main() -> None:
    cfg = settings()
    log = configure_logging("ebook-reconcile", cfg.log_dir)
    await db.init_db(cfg.db_path)

    log.info("reconciliation loop started")
    while True:
        try:
            jobs = await db.fetchall(
                cfg.db_path,
                "SELECT * FROM jobs WHERE status IN ('ingesting','translating','reassembling')",
            )
            now = time.time()
            for job in jobs:
                age = now - job["created_at"]
                if age < 300:
                    continue

                done_row = await db.fetchone(
                    cfg.db_path,
                    "SELECT COUNT(*) as c FROM chunks WHERE job_id=? AND status IN ('done','failed')",
                    (job["job_id"],),
                )
                done = done_row["c"] if done_row else 0

                old_row = await db.fetchone(
                    cfg.db_path,
                    "SELECT COUNT(*) as c FROM chunks WHERE job_id=? AND status='pending'",
                    (job["job_id"],),
                )
                pending = old_row["c"] if old_row else 0

                if done == 0 and pending == 0 and age > 600:
                    log.warning(f"job={job['job_id']} stuck in {job['status']} with no chunks for {int(age)}s")

                if job["status"] == "translating" and done == 0 and age > 1800:
                    log.warning(f"job={job['job_id']} translating with 0 progress for {int(age)}s - providers may all be cooling")

                translating_row = await db.fetchone(
                    cfg.db_path,
                    "SELECT COUNT(*) as c FROM chunks WHERE job_id=? AND status='translating'",
                    (job["job_id"],),
                )
                stuck = translating_row["c"] if translating_row else 0
                if stuck > 0:
                    chunks = await db.fetchall(
                        cfg.db_path,
                        "SELECT chunk_id, updated_at FROM chunks WHERE job_id=? AND status='translating'",
                        (job["job_id"],),
                    )
                    for chunk in chunks:
                        updated = chunk["updated_at"] or 0
                        if now - updated > 600:
                            await db.execute(
                                cfg.db_path,
                                "UPDATE chunks SET status='pending' WHERE job_id=? AND chunk_id=?",
                                (job["job_id"], chunk["chunk_id"]),
                            )
                            log.info(f"reclaimed stuck chunk job={job['job_id']} chunk={chunk['chunk_id']}")

        except Exception as exc:
            log.exception(f"reconciliation error: {exc}")

        await asyncio.sleep(600)


if __name__ == "__main__":
    asyncio.run(main())
