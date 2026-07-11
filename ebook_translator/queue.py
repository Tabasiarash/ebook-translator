from __future__ import annotations

import asyncio

import redis.asyncio as redis
from redis.exceptions import ResponseError


async def connect(redis_url: str) -> redis.Redis:
    return redis.from_url(redis_url, encoding="utf-8", decode_responses=True)


async def ensure_group(r: redis.Redis, stream: str, group: str) -> None:
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def read_group(
    r: redis.Redis,
    stream: str,
    group: str,
    consumer: str,
    *,
    count: int = 1,
    block_ms: int = 5000,
):
    return await r.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)


async def pending_depth(r: redis.Redis, *streams: str) -> int:
    total = 0
    for stream in streams:
        try:
            total += int(await r.xlen(stream))
        except Exception:
            continue
    return total


async def sleep_forever_on_empty() -> None:
    await asyncio.sleep(1)

