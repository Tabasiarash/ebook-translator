from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import pytest_asyncio

from ebook_translator.config import COOLDOWN_ZSET
from ebook_translator.providers import (
    ProviderKey,
    RateLimited,
    effective_rpm,
    load_provider_keys,
    mark_cooldown,
    pick_available,
    sleep_until_next_available,
)


class TestLoadProviderKeys:
    def test_loads_keys_from_yaml(self, mock_providers_yaml: Path):
        keys = load_provider_keys(mock_providers_yaml)
        assert len(keys) > 0

    def test_keys_have_correct_structure(self, mock_providers_yaml: Path):
        keys = load_provider_keys(mock_providers_yaml)
        for key in keys:
            assert isinstance(key, ProviderKey)
            assert isinstance(key.name, str)
            assert isinstance(key.key, str)
            assert isinstance(key.model, str)
            assert isinstance(key.rpm_limit, int)
            assert isinstance(key.priority, int)

    def test_sorted_by_priority(self, mock_providers_yaml: Path):
        keys = load_provider_keys(mock_providers_yaml)
        priorities = [k.priority for k in keys]
        assert priorities == sorted(priorities)

    def test_returns_empty_for_missing_file(self):
        assert load_provider_keys(Path("/nonexistent.yaml")) == []

    def test_gemini_keys_loaded(self, mock_providers_yaml: Path):
        keys = load_provider_keys(mock_providers_yaml)
        gemini = [k for k in keys if k.name == "gemini"]
        assert len(gemini) == 1
        assert gemini[0].model == "gemini-2.0-flash"

    def test_groq_keys_loaded(self, mock_providers_yaml: Path):
        keys = load_provider_keys(mock_providers_yaml)
        groq = [k for k in keys if k.name == "groq"]
        assert len(groq) == 2


class TestProviderKey:
    def test_identity_masks_key(self):
        key = ProviderKey("test", "abc12345secret67890", "model-x", 10, 1)
        # Last 8 chars of the key are "ret67890"
        assert key.identity == "test:model-x:ret67890"

    def test_identity_unique_per_provider_model_key(self):
        k1 = ProviderKey("a", "key1", "m1", 10, 1)
        k2 = ProviderKey("a", "key2", "m1", 10, 1)
        assert k1.identity != k2.identity


@pytest_asyncio.fixture
async def clean_cooldowns(redis_conn):
    await redis_conn.delete(COOLDOWN_ZSET)


class TestPickAvailable:
    async def test_returns_first_available(self, clean_cooldowns, redis_conn, mock_providers_yaml):
        keys = load_provider_keys(mock_providers_yaml)
        picked = await pick_available(redis_conn, keys)
        assert picked is not None
        assert picked.name == "gemini"

    async def test_skips_cooldown_keys(self, clean_cooldowns, redis_conn, mock_providers_yaml):
        keys = load_provider_keys(mock_providers_yaml)
        gemini = [k for k in keys if k.name == "gemini"][0]
        await mark_cooldown(redis_conn, gemini, 300)
        picked = await pick_available(redis_conn, keys)
        assert picked is not None
        assert picked.name == "groq"

    async def test_returns_none_when_all_cooldown(self, clean_cooldowns, redis_conn, mock_providers_yaml):
        keys = load_provider_keys(mock_providers_yaml)
        for k in keys:
            await mark_cooldown(redis_conn, k, 300)
        picked = await pick_available(redis_conn, keys)
        assert picked is None


class TestMarkCooldown:
    async def test_sets_cooldown(self, clean_cooldowns, redis_conn, mock_providers_yaml):
        keys = load_provider_keys(mock_providers_yaml)
        key = keys[0]
        await mark_cooldown(redis_conn, key, 60)
        score = await redis_conn.zscore(COOLDOWN_ZSET, key.identity)
        assert score is not None
        assert int(score) >= int(time.time()) + 59


class TestEffectiveRPM:
    async def test_all_keys_contributor(self, clean_cooldowns, redis_conn, mock_providers_yaml):
        keys = load_provider_keys(mock_providers_yaml)
        rpm = await effective_rpm(redis_conn, keys)
        total_expected = sum(k.rpm_limit for k in keys)
        assert rpm == total_expected

    async def test_cooldown_reduces_rpm(self, clean_cooldowns, redis_conn, mock_providers_yaml):
        keys = load_provider_keys(mock_providers_yaml)
        for k in keys:
            await mark_cooldown(redis_conn, k, 300)
        rpm = await effective_rpm(redis_conn, keys)
        assert rpm == 1  # min 1
