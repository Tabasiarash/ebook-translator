from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import yaml

from .config import COOLDOWN_ZSET


FREE_MODEL_CACHE: tuple[float, list[str]] = (0, [])
MODEL_PREFERENCE = (
    "gpt-oss-120b",
    "llama-3.3-70b",
    "hermes-3",
    "qwen3-next",
    "gemma-4",
    "nemotron-3-super",
    "gpt-oss-20b",
    "llama-3.2",
)
MODEL_PENALTY = ("code", "coder", "safety", "vl", "vision", "omni", "audio", "image")
MODEL_EXCLUDE = ("lyria",)


class RateLimited(Exception):
    def __init__(self, retry_after: int = 60):
        super().__init__("rate limited")
        self.retry_after = retry_after


@dataclass(frozen=True)
class ProviderKey:
    name: str
    key: str
    model: str
    rpm_limit: int
    priority: int

    @property
    def identity(self) -> str:
        return f"{self.name}:{self.model}:{self.key[-8:]}"


def load_provider_keys(path: Path) -> list[ProviderKey]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    keys: list[ProviderKey] = []
    for provider in data.get("providers", []):
        raw_keys = provider.get("keys") or []
        if provider.get("keys_env"):
            raw_keys.extend(_env_list(provider["keys_env"]))
        models = provider.get("models") or [provider.get("model")]
        for key in raw_keys:
            for model in [item for item in models if item]:
                if not key:
                    continue
                keys.append(
                    ProviderKey(
                        name=provider["name"],
                        key=key,
                        model=model,
                        rpm_limit=int(provider.get("rpm_limit", 10)),
                        priority=int(provider.get("priority", 100)),
                    )
                )
    return sorted(keys, key=lambda item: item.priority)


async def load_provider_keys_async(path: Path) -> list[ProviderKey]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    keys: list[ProviderKey] = []
    for provider in data.get("providers", []):
        raw_keys = provider.get("keys") or []
        if provider.get("keys_env"):
            raw_keys.extend(_env_list(provider["keys_env"]))
        models = provider.get("models") or [provider.get("model")]
        if provider.get("name") == "openrouter" and provider.get("auto_free_models"):
            models = await discover_openrouter_free_models() or models or ["openrouter/free"]
        for key in raw_keys:
            for model in [item for item in models if item]:
                if not key:
                    continue
                keys.append(
                    ProviderKey(
                        name=provider["name"],
                        key=key,
                        model=model,
                        rpm_limit=int(provider.get("rpm_limit", 10)),
                        priority=int(provider.get("priority", 100)),
                    )
                )
    return sorted(keys, key=lambda item: (item.priority, _model_rank(item.model)))


async def discover_openrouter_free_models() -> list[str]:
    global FREE_MODEL_CACHE
    now = time.time()
    if FREE_MODEL_CACHE[1] and now - FREE_MODEL_CACHE[0] < 3600:
        return FREE_MODEL_CACHE[1]
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get("https://openrouter.ai/api/v1/models") as resp:
                data = await resp.json()
                if resp.status >= 400:
                    return FREE_MODEL_CACHE[1]
    except Exception:
        return FREE_MODEL_CACHE[1]
    models = []
    for item in data.get("data", []):
        model_id = item.get("id", "")
        pricing = item.get("pricing") or {}
        if not model_id:
            continue
        if any(token in model_id.lower() for token in MODEL_EXCLUDE):
            continue
        architecture = item.get("architecture") or {}
        if architecture:
            inputs = set(architecture.get("input_modalities") or [])
            outputs = set(architecture.get("output_modalities") or [])
            if "text" not in inputs or outputs != {"text"}:
                continue
        if not _is_zero_price(pricing.get("prompt")) or not _is_zero_price(pricing.get("completion")):
            continue
        models.append(model_id)
    ranked = sorted(set(models), key=_model_rank)
    if ranked:
        FREE_MODEL_CACHE = (now, ranked[:12])
    return FREE_MODEL_CACHE[1]


def _is_zero_price(value: object) -> bool:
    try:
        return float(str(value)) == 0.0
    except Exception:
        return False


def _model_rank(model: str) -> tuple[int, int, str]:
    lowered = model.lower()
    penalty = 50 if any(token in lowered for token in MODEL_PENALTY) else 0
    for index, token in enumerate(MODEL_PREFERENCE):
        if token in lowered:
            return (penalty, index, lowered)
    if model == "openrouter/free":
        return (25, 999, lowered)
    return (penalty + 10, 500, lowered)


def _env_list(name: str) -> list[str]:
    return [part.strip() for part in os.getenv(name, "").split(",") if part.strip()]


async def pick_available(r, keys: list[ProviderKey]) -> ProviderKey | None:
    now = time.time()
    for item in keys:
        available_at = await r.zscore(COOLDOWN_ZSET, item.identity)
        if available_at is None or float(available_at) <= now:
            return item
    return None


async def sleep_until_next_available(r) -> None:
    rows = await r.zrange(COOLDOWN_ZSET, 0, 0, withscores=True)
    if not rows:
        await asyncio.sleep(5)
        return
    delay = max(1, int(rows[0][1] - time.time()))
    await asyncio.sleep(min(delay, 300))


async def mark_cooldown(r, provider_key: ProviderKey, seconds: int) -> None:
    await r.zadd(COOLDOWN_ZSET, {provider_key.identity: int(time.time()) + max(seconds, 1)})


async def effective_rpm(r, keys: list[ProviderKey]) -> int:
    now = time.time()
    total = 0
    for item in keys:
        available_at = await r.zscore(COOLDOWN_ZSET, item.identity)
        if available_at is None or float(available_at) <= now:
            total += item.rpm_limit
    return max(total, 1)


async def translate_text(provider_key: ProviderKey, target_language: str, chunk_text: str, context: Optional[str] = None, system_override: Optional[str] = None) -> str:
    if system_override:
        system = system_override
    else:
        system = (
            "You are a professional literary translator. Translate the following book excerpt into "
            f"{target_language}. Preserve paragraph breaks exactly. Preserve any inline markers like "
            "[IMG_3] or [HEADING] unchanged. Do not add commentary, notes, or explanations - output "
            "ONLY the translated text. Maintain the tone, register, and style of the original."
        )
    if context:
        system += f"\n\nUse the following context to ensure consistency:\n{context}"

    if provider_key.name == "gemini":
        return await _gemini(provider_key, system, chunk_text)
    if provider_key.name == "groq":
        return await _openai_compatible(
            "https://api.groq.com/openai/v1/chat/completions", provider_key, system, chunk_text
        )
    if provider_key.name == "openrouter":
        return await _openai_compatible(
            "https://openrouter.ai/api/v1/chat/completions", provider_key, system, chunk_text
        )
    if provider_key.name == "mistral":
        return await _openai_compatible(
            "https://api.mistral.ai/v1/chat/completions", provider_key, system, chunk_text
        )
    raise RuntimeError(f"unsupported provider {provider_key.name}")


async def _gemini(provider_key: ProviderKey, system: str, text: str) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{provider_key.model}:"
        f"generateContent?key={provider_key.key}"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        async with session.post(url, json=payload) as resp:
            if resp.status == 429:
                raise RateLimited(int(resp.headers.get("retry-after", "60")))
            data: dict[str, Any] = await resp.json()
            if resp.status >= 400:
                raise RuntimeError(str(data)[:1000])
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()


async def _openai_compatible(url: str, provider_key: ProviderKey, system: str, text: str) -> str:
    payload = {
        "model": provider_key.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {provider_key.key}", "Content-Type": "application/json"}
    if provider_key.name == "openrouter":
        headers["HTTP-Referer"] = "https://ebook-translator.local"
        headers["X-Title"] = "PDF Ebook Translator Bot"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 429:
                raise RateLimited(int(resp.headers.get("retry-after", "60")))
            data: dict[str, Any] = await resp.json()
            if resp.status >= 400:
                raise RuntimeError(str(data)[:1000])
            return data["choices"][0]["message"]["content"].strip()
