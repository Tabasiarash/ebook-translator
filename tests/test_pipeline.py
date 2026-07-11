from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from ebook_translator import db
from ebook_translator.languages import mode_for
from ebook_translator.pdf import build_chunks, extract_profile, write_profile


async def simulate_ingest(db_path: Path, job_id: str, source_pdf: Path, target_lang: str) -> dict[str, Any]:
    profile = extract_profile(source_pdf, target_lang, "English")
    job_dir = Path(tempfile.gettempdir()) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    write_profile(job_dir, profile)
    chunks = build_chunks(profile)

    total_chunks = 0
    for chunk in chunks:
        await db.upsert_chunk(
            db_path,
            {
                "job_id": job_id,
                "chunk_id": str(chunk["chunk_id"]),
                "page_num": chunk["page_id"],
                "block_id": ",".join(chunk["block_ids"]),
                "source_text": chunk["text"],
            },
        )
        total_chunks += 1

    await db.update_job(
        db_path,
        job_id,
        status="translating",
        total_pages=profile["page_count"],
        total_chunks=total_chunks,
        mode=profile["mode"],
    )

    return {"chunks": chunks, "total_chunks": total_chunks, "profile": profile}


async def simulate_translate(db_path: Path, job_id: str, chunks: list[dict]) -> int:
    translated = 0
    for chunk in chunks:
        translated_text = f"[TRANS] {chunk['text']}"
        await db.execute(
            db_path,
            "UPDATE chunks SET translated_text=?, status='done' WHERE job_id=? AND chunk_id=?",
            (translated_text, job_id, str(chunk["chunk_id"])),
        )
        translated += 1
    return translated


@pytest.mark.asyncio
async def test_full_pipeline(simple_pdf: Path, test_db: Path):
    job_id = "test-pipeline-job"
    target_lang = "German"

    await db.create_job(
        test_db,
        {
            "job_id": job_id,
            "user_id": "test-user",
            "chat_id": 12345,
            "source_lang": "English",
            "target_lang": target_lang,
        },
    )

    ingest_result = await simulate_ingest(test_db, job_id, simple_pdf, target_lang)
    assert ingest_result["total_chunks"] > 0

    translated = await simulate_translate(test_db, job_id, ingest_result["chunks"])
    assert translated == ingest_result["total_chunks"]

    done_row = await db.fetchone(
        test_db,
        "SELECT COUNT(*) as c FROM chunks WHERE job_id=? AND status='done'",
        (job_id,),
    )
    assert done_row["c"] == translated

    glossary_terms = [
        ("Wigglesworth Corporation", "Wigglesworth Corporation", "name", 0),
        ("Quantum Engine", "Quantenmotor", "invented_term", 0),
    ]
    for source, target, ttype, chunk in glossary_terms:
        await db.execute(
            test_db,
            "INSERT OR IGNORE INTO glossary (job_id, source_term, target_term, term_type, first_seen_chunk) VALUES (?, ?, ?, ?, ?)",
            (job_id, source, target, ttype, chunk),
        )

    glossary_rows = await db.fetchall(test_db, "SELECT * FROM glossary WHERE job_id=?", (job_id,))
    assert len(glossary_rows) == 2

    for row in glossary_rows:
        assert row["target_term"] != ""

    await db.update_job(test_db, job_id, status="done", completed_at=1000000)
    final_job = await db.fetchone(test_db, "SELECT * FROM jobs WHERE job_id=?", (job_id,))
    assert final_job["status"] == "done"


@pytest.mark.asyncio
async def test_pipeline_glossary_consistency(simple_pdf: Path, test_db: Path):
    job_id = "test-glossary-consistency"
    target_lang = "French"

    await db.create_job(
        test_db,
        {
            "job_id": job_id,
            "user_id": "test-user",
            "chat_id": 12345,
            "source_lang": "English",
            "target_lang": target_lang,
        },
    )

    profile = extract_profile(simple_pdf, target_lang, "English")
    chunks = build_chunks(profile)
    for chunk in chunks:
        await db.upsert_chunk(
            test_db,
            {
                "job_id": job_id,
                "chunk_id": str(chunk["chunk_id"]),
                "page_num": chunk["page_id"],
                "block_id": ",".join(chunk["block_ids"]),
                "source_text": chunk["text"],
            },
        )

    glossary_terms = [
        ("Quantum Engine", "Moteur Quantique", "invented_term", 0),
        ("Aeroville", "Aérovil", "place", 0),
    ]
    for source, target, ttype, chunk in glossary_terms:
        await db.execute(
            test_db,
            "INSERT OR IGNORE INTO glossary (job_id, source_term, target_term, term_type, first_seen_chunk) VALUES (?, ?, ?, ?, ?)",
            (job_id, source, target, ttype, chunk),
        )

    glossary_rows = await db.fetchall(test_db, "SELECT * FROM glossary WHERE job_id=?", (job_id,))
    assert len(glossary_rows) == 2

    for chunk in chunks:
        chunk_glossary = await db.fetchall(
            test_db,
            "SELECT source_term, target_term FROM glossary WHERE job_id=?",
            (job_id,),
        )
        if chunk_glossary:
            context_lines = [f"- {g['source_term']} → {g['target_term']}" for g in chunk_glossary]
            context = "\n".join(context_lines)
            assert "Quantum Engine → Moteur Quantique" in context
            assert "Aeroville → Aérovil" in context

    await db.update_job(test_db, job_id, status="done")
    final = await db.fetchone(test_db, "SELECT * FROM jobs WHERE job_id=?", (job_id,))
    assert final["status"] == "done"


@pytest.mark.asyncio
async def test_pipeline_cancel_resume(simple_pdf: Path, test_db: Path):
    job_id = "test-cancel-resume"

    await db.create_job(
        test_db,
        {
            "job_id": job_id,
            "user_id": "test-user",
            "chat_id": 12345,
            "source_lang": "English",
            "target_lang": "Spanish",
        },
    )

    profile = extract_profile(simple_pdf, "Spanish", "English")
    chunks = build_chunks(profile)
    for i, chunk in enumerate(chunks):
        status = "done" if i < len(chunks) // 2 else "failed"
        await db.upsert_chunk(
            test_db,
            {
                "job_id": job_id,
                "chunk_id": str(chunk["chunk_id"]),
                "page_num": chunk["page_id"],
                "block_id": ",".join(chunk["block_ids"]),
                "source_text": chunk["text"],
                "status": status,
            },
        )

    await db.update_job(test_db, job_id, status="translating")

    failed_before = await db.fetchall(
        test_db, "SELECT chunk_id FROM chunks WHERE job_id=? AND status='failed'", (job_id,)
    )
    assert len(failed_before) > 0

    await db.execute(
        test_db,
        "UPDATE chunks SET status='pending' WHERE job_id=? AND status='failed'",
        (job_id,),
    )

    pending = await db.fetchall(
        test_db, "SELECT chunk_id FROM chunks WHERE job_id=? AND status='pending'", (job_id,)
    )
    assert len(pending) == len(failed_before)

    await db.update_job(test_db, job_id, status="translating")
    job = await db.fetchone(test_db, "SELECT * FROM jobs WHERE job_id=?", (job_id,))
    assert job["status"] == "translating"
