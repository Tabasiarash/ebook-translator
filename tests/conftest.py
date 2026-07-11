from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

from ebook_translator import db
from ebook_translator.config import settings
from ebook_translator.languages import mode_for
from ebook_translator.pdf import extract_profile, build_chunks, write_profile

cfg = settings()

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def simple_pdf() -> Path:
    return FIXTURE_DIR / "test_simple.pdf"


@pytest.fixture(scope="session")
def multi_chapter_pdf() -> Path:
    return FIXTURE_DIR / "test_multi_chapter.pdf"


@pytest_asyncio.fixture
async def test_db() -> AsyncGenerator[Path, None]:
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
        db_path = Path(f.name)
    await db.init_db(db_path)
    try:
        yield db_path
    finally:
        db_path.unlink(missing_ok=True)


@pytest_asyncio.fixture
async def test_job_dir() -> AsyncGenerator[Path, None]:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def english_profile(simple_pdf: Path) -> dict[str, Any]:
    return extract_profile(simple_pdf, "German", "English")


@pytest.fixture
def english_chunks(english_profile: dict[str, Any]) -> list[dict[str, Any]]:
    return build_chunks(english_profile)


@pytest.fixture
def profile_french(simple_pdf: Path) -> dict[str, Any]:
    return extract_profile(simple_pdf, "French", "English")


@pytest.fixture
def profile_arabic(simple_pdf: Path) -> dict[str, Any]:
    return extract_profile(simple_pdf, "Arabic", "English")


@pytest.fixture
def redis_url() -> str:
    return cfg.redis_url


@pytest.fixture
def mock_providers_yaml(tmp_path: Path) -> Path:
    data = {
        "providers": [
            {
                "name": "gemini",
                "keys": ["test-key-gemini-1"],
                "models": ["gemini-2.0-flash"],
                "rpm_limit": 10,
                "priority": 1,
            },
            {
                "name": "groq",
                "keys": ["test-key-groq-1", "test-key-groq-2"],
                "models": ["llama-3.3-70b"],
                "rpm_limit": 30,
                "priority": 2,
            },
        ]
    }
    path = tmp_path / "providers.yaml"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
