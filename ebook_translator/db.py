from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  user_id TEXT,
  chat_id INTEGER,
  source_lang TEXT,
  target_lang TEXT,
  mode TEXT,
  total_pages INTEGER,
  total_chunks INTEGER,
  status TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  completed_at INTEGER
);

CREATE TABLE IF NOT EXISTS chunks (
  job_id TEXT NOT NULL,
  chunk_id TEXT NOT NULL,
  page_num INTEGER,
  block_id TEXT,
  bbox TEXT,
  source_text TEXT NOT NULL,
  translated_text TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  provider_used TEXT,
  updated_at INTEGER,
  PRIMARY KEY (job_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_chunks_job_status ON chunks(job_id, status);

CREATE TABLE IF NOT EXISTS glossary (
  job_id TEXT NOT NULL,
  source_term TEXT NOT NULL,
  target_term TEXT NOT NULL DEFAULT '',
  term_type TEXT,
  first_seen_chunk INTEGER,
  PRIMARY KEY (job_id, source_term)
);
"""


async def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def execute(path: Path, sql: str, params: tuple[Any, ...] = ()) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(sql, params)
        await db.commit()


async def fetchone(path: Path, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None


async def fetchall(path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        return [dict(row) for row in await cur.fetchall()]


async def create_job(path: Path, job: dict[str, Any]) -> None:
    now = int(time.time())
    await execute(
        path,
        """INSERT INTO jobs
        (job_id, user_id, chat_id, source_lang, target_lang, status, total_pages, total_chunks, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job["job_id"],
            str(job.get("user_id", "")),
            job.get("chat_id"),
            job.get("source_lang", "English"),
            job.get("target_lang", ""),
            "ingesting",
            0,
            0,
            now,
        ),
    )


async def update_job(path: Path, job_id: str, **fields: Any) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{k}=?" for k in fields)
    await execute(path, f"UPDATE jobs SET {assignments} WHERE job_id=?", (*fields.values(), job_id))


async def upsert_chunk(path: Path, row: dict[str, Any]) -> None:
    await execute(
        path,
        """INSERT OR REPLACE INTO chunks
        (job_id, chunk_id, page_num, block_id, bbox, source_text, translated_text, status, provider_used, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row["job_id"],
            str(row["chunk_id"]),
            row.get("page_num"),
            row.get("block_id"),
            row.get("bbox"),
            row.get("source_text", ""),
            row.get("translated_text"),
            row.get("status", "pending"),
            row.get("provider_used"),
            int(time.time()),
        ),
    )


# Counts are queried from the chunks table directly, not stored redundantly.
