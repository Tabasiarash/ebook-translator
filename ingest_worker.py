from __future__ import annotations

import asyncio
import socket

from ebook_translator import db
from ebook_translator.config import GROUP_INGEST, STREAM_INGEST, STREAM_TRANSLATE, settings
from ebook_translator.logging_config import configure_logging
from ebook_translator.pdf import build_chunks, extract_profile, write_profile
from ebook_translator.providers import (
    RateLimited,
    load_provider_keys_async,
    mark_cooldown,
    pick_available,
    sleep_until_next_available,
    translate_text,
)
from ebook_translator.queue import connect, ensure_group, read_group


GLOSSARY_EXTRACTION_SYSTEM = (
    "You are a glossary extraction assistant. Given a book excerpt, extract all proper nouns, "
    "invented terms, recurring phrases, and domain-specific vocabulary that should be translated "
    "consistently throughout the book. Return ONLY a JSON array of objects, each with keys: "
    "source_term (string), term_type (one of: name, place, invented_term, recurring_phrase). "
    "Include terms that appear 3+ times across the full book. No explanation, no markdown."
)

GLOSSARY_TRANSLATE_SYSTEM = (
    "You are a professional translator. Translate the following glossary terms into {target_lang}. "
    "Return ONLY a JSON array of objects, each with keys: source_term, target_term. "
    "Preserve names phonetically, translate invented terms consistently. No explanation, no markdown."
)


async def extract_glossary(cfg, r, job_id: str, full_text: str, log) -> None:
    import json

    provider_keys = await load_provider_keys_async(cfg.providers_path)
    if not provider_keys:
        log.warning("no provider keys for glossary extraction")
        return

    batches = []
    words = full_text.split()
    for i in range(0, len(words), 2000):
        batches.append(" ".join(words[i:i + 2000]))

    seen_terms: dict[str, tuple[str, int]] = {}

    for batch_idx, batch in enumerate(batches):
        for attempt in range(3):
            key = await pick_available(r, provider_keys)
            if not key:
                await sleep_until_next_available(r)
                continue
            try:
                result = await translate_text(key, "", batch, system_override=GLOSSARY_EXTRACTION_SYSTEM)
                result_clean = result.strip()
                if result_clean.startswith("```"):
                    result_clean = result_clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                terms = json.loads(result_clean)
                for term in terms:
                    source = term["source_term"].strip()
                    ttype = term.get("term_type", "name")
                    if source not in seen_terms:
                        seen_terms[source] = (ttype, batch_idx)
                break
            except RateLimited as exc:
                await mark_cooldown(r, key, exc.retry_after)
                log.warning(f"glossary rate limited {key.identity}: {exc.retry_after}s")
            except (json.JSONDecodeError, Exception) as exc:
                log.warning(f"glossary extraction batch {batch_idx} attempt {attempt}: {exc}")
                await asyncio.sleep(2 ** attempt)

    for source, (ttype, first_batch) in seen_terms.items():
        await db.execute(
            cfg.db_path,
            "INSERT OR IGNORE INTO glossary (job_id, source_term, target_term, term_type, first_seen_chunk) "
            "VALUES (?, ?, '', ?, ?)",
            (job_id, source, ttype, first_batch),
        )

    log.info(f"job={job_id} glossary extracted {len(seen_terms)} terms")


async def translate_glossary(cfg, r, job_id: str, target_lang: str, log) -> None:
    import json

    rows = await db.fetchall(
        cfg.db_path,
        "SELECT source_term FROM glossary WHERE job_id=? AND target_term=''",
        (job_id,),
    )
    if not rows:
        return

    provider_keys = await load_provider_keys_async(cfg.providers_path)
    terms_str = "\n".join(f"- {r['source_term']}" for r in rows)
    prompt = f"Translate these glossary terms into {target_lang}:\n{terms_str}"
    system = GLOSSARY_TRANSLATE_SYSTEM.format(target_lang=target_lang)

    for attempt in range(3):
        key = await pick_available(r, provider_keys)
        if not key:
            await sleep_until_next_available(r)
            continue
        try:
            result = await translate_text(key, "", prompt, system_override=system)
            result_clean = result.strip()
            if result_clean.startswith("```"):
                result_clean = result_clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            translations = json.loads(result_clean)
            for t in translations:
                source = t.get("source_term", "").strip()
                target = t.get("target_term", "").strip()
                if source and target:
                    await db.execute(
                        cfg.db_path,
                        "UPDATE glossary SET target_term=? WHERE job_id=? AND source_term=?",
                        (target, job_id, source),
                    )
            log.info(f"job={job_id} glossary translated {len(translations)} terms")
            return
        except RateLimited as exc:
            await mark_cooldown(r, key, exc.retry_after)
            log.warning(f"glossary translate rate limited {key.identity}: {exc.retry_after}s")
        except Exception as exc:
            log.warning(f"glossary translate attempt {attempt}: {exc}")
            await asyncio.sleep(2 ** attempt)


async def main() -> None:
    cfg = settings()
    log = configure_logging("ebook-ingest", cfg.log_dir)
    await db.init_db(cfg.db_path)
    r = await connect(cfg.redis_url)
    await ensure_group(r, STREAM_INGEST, GROUP_INGEST)
    consumer = socket.gethostname()
    log.info("ingest worker started")
    while True:
        rows = await read_group(r, STREAM_INGEST, GROUP_INGEST, consumer)
        if not rows:
            continue
        for stream, messages in rows:
            for msg_id, data in messages:
                job_id = data["job_id"]
                try:
                    job = await db.fetchone(cfg.db_path, "SELECT * FROM jobs WHERE job_id=?", (job_id,))
                    if not job or job["status"] == "cancelled":
                        await r.xack(stream, GROUP_INGEST, msg_id)
                        continue
                    await db.update_job(cfg.db_path, job_id, status="ingesting")
                    job_dir = cfg.jobs_dir / job_id
                    profile = extract_profile(job_dir / "source.pdf", job["target_lang"])
                    write_profile(job_dir, profile)
                    chunks = build_chunks(profile)
                    for chunk in chunks:
                        await db.upsert_chunk(
                            cfg.db_path,
                            dict(
                                job_id=job_id,
                                chunk_id=str(chunk["chunk_id"]),
                                page_num=chunk["page_id"],
                                block_id=",".join(chunk["block_ids"]),
                                source_text=chunk["text"],
                            ),
                        )

                    full_text = "\n\n".join(c["text"] for c in chunks)
                    await extract_glossary(cfg, r, job_id, full_text, log)

                    await translate_glossary(cfg, r, job_id, job["target_lang"], log)

                    for chunk in chunks:
                        await r.xadd(
                            STREAM_TRANSLATE,
                            {
                                "job_id": job_id,
                                "chunk_id": str(chunk["chunk_id"]),
                                "text": chunk["text"],
                            },
                        )

                    await db.update_job(
                        cfg.db_path,
                        job_id,
                        status="translating",
                        total_pages=profile["page_count"],
                        total_chunks=len(chunks),
                        mode=profile["mode"],
                    )
                    await r.xack(stream, GROUP_INGEST, msg_id)
                    log.info(f"job={job_id} extracted chunks={len(chunks)}")
                except Exception as exc:
                    await db.update_job(cfg.db_path, job_id, status="failed")
                    log.exception(f"job={job_id} ingest failed: {exc}")
                    await r.xack(stream, GROUP_INGEST, msg_id)


if __name__ == "__main__":
    asyncio.run(main())
