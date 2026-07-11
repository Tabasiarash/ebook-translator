from __future__ import annotations

import json
from pathlib import Path

import pytest

from ebook_translator import db


class TestGlossarySchema:
    async def test_create_glossary_entry(self, test_db: Path):
        await db.execute(
            test_db,
            "INSERT INTO glossary (job_id, source_term, target_term, term_type, first_seen_chunk) VALUES (?, ?, ?, ?, ?)",
            ("test-job", "Quantum Engine", "Quantenmotor", "invented_term", 0),
        )
        row = await db.fetchone(
            test_db, "SELECT * FROM glossary WHERE job_id=? AND source_term=?", ("test-job", "Quantum Engine")
        )
        assert row is not None
        assert row["target_term"] == "Quantenmotor"
        assert row["term_type"] == "invented_term"

    async def test_glossary_unique_per_job(self, test_db: Path):
        await db.execute(
            test_db,
            "INSERT INTO glossary (job_id, source_term, target_term, term_type) VALUES (?, ?, ?, ?)",
            ("job1", "Quantum Engine", "QE1", "invented_term"),
        )
        await db.execute(
            test_db,
            "INSERT OR IGNORE INTO glossary (job_id, source_term, target_term, term_type) VALUES (?, ?, ?, ?)",
            ("job1", "Quantum Engine", "QE2", "invented_term"),
        )
        rows = await db.fetchall(
            test_db,
            "SELECT * FROM glossary WHERE job_id=? AND source_term=?",
            ("job1", "Quantum Engine"),
        )
        assert len(rows) == 1

    async def test_multiple_jobs_same_term(self, test_db: Path):
        await db.execute(
            test_db,
            "INSERT INTO glossary (job_id, source_term, target_term, term_type) VALUES (?, ?, ?, ?)",
            ("job1", "Aeroville", "Aeroville", "place"),
        )
        await db.execute(
            test_db,
            "INSERT INTO glossary (job_id, source_term, target_term, term_type) VALUES (?, ?, ?, ?)",
            ("job2", "Aeroville", "Aeroville", "place"),
        )
        rows = await db.fetchall(test_db, "SELECT * FROM glossary WHERE source_term=?", ("Aeroville",))
        assert len(rows) == 2

    async def test_update_glossary_translation(self, test_db: Path):
        await db.execute(
            test_db,
            "INSERT INTO glossary (job_id, source_term, target_term, term_type) VALUES (?, ?, ?, ?)",
            ("test-job", "Zephyr Protocol", "", "invented_term"),
        )
        await db.execute(
            test_db,
            "UPDATE glossary SET target_term=? WHERE job_id=? AND source_term=?",
            ("Zephyr-Protokoll", "test-job", "Zephyr Protocol"),
        )
        row = await db.fetchone(
            test_db, "SELECT * FROM glossary WHERE job_id=? AND source_term=?", ("test-job", "Zephyr Protocol")
        )
        assert row["target_term"] == "Zephyr-Protokoll"


class TestGlossaryExtraction:
    FULL_TEXT = (
        "This is about Dr. Archimedes Q. Wigglesworth of the Wigglesworth Corporation. "
        "He invented the Quantum Engine as part of Project Stardust. "
        "The Zephyr Protocol was implemented at Aeroville, the floating city. "
        "Dr. Wigglesworth's Quantum Engine uses the Zephyr Protocol for operation. "
        "Project Stardust is headquartered at Aeroville. "
        "These proper nouns should appear multiple times in this text."
    )

    async def test_glossary_terms_extracted(self, test_db: Path):
        seen_terms = {
            "Dr. Archimedes Q. Wigglesworth": ("name", 0),
            "Wigglesworth Corporation": ("name", 0),
            "Quantum Engine": ("invented_term", 0),
            "Project Stardust": ("invented_term", 0),
            "Zephyr Protocol": ("invented_term", 0),
            "Aeroville": ("place", 0),
        }
        for source, (ttype, batch) in seen_terms.items():
            await db.execute(
                test_db,
                "INSERT OR IGNORE INTO glossary (job_id, source_term, target_term, term_type, first_seen_chunk) VALUES (?, ?, '', ?, ?)",
                ("test-job", source, ttype, batch),
            )
        rows = await db.fetchall(test_db, "SELECT * FROM glossary WHERE job_id=?", ("test-job",))
        assert len(rows) == 6

    async def test_glossary_terms_not_duplicated(self, test_db: Path):
        for i in range(3):
            await db.execute(
                test_db,
                "INSERT OR IGNORE INTO glossary (job_id, source_term, target_term, term_type) VALUES (?, ?, ?, ?)",
                ("test-job", "Quantum Engine", "", "invented_term"),
            )
        rows = await db.fetchall(
            test_db,
            "SELECT * FROM glossary WHERE job_id=? AND source_term=?",
            ("test-job", "Quantum Engine"),
        )
        assert len(rows) == 1

    async def test_glossary_injection_into_prompt(self):
        glossary_entries = [
            {"source_term": "Quantum Engine", "target_term": "Quantenmotor"},
            {"source_term": "Aeroville", "target_term": "Aeroville"},
        ]
        prompt_parts = ["Translate the following text."]
        context_lines = [f"- {e['source_term']} → {e['target_term']}" for e in glossary_entries]
        prompt_parts.append("\nGlossary consistency:\n" + "\n".join(context_lines))
        prompt = "\n\n".join(prompt_parts)
        assert "Quantum Engine → Quantenmotor" in prompt
        assert "Aeroville → Aeroville" in prompt
