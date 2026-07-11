from __future__ import annotations

import pytest

from ebook_translator.queue import connect, ensure_group, pending_depth, read_group
from ebook_translator.config import STREAM_INGEST, GROUP_INGEST


class TestRedisStream:
    async def test_connect(self, redis_url):
        r = await connect(redis_url)
        assert await r.ping()
        await r.aclose()

    async def test_ensure_group_creates_stream(self, redis_conn):
        stream = f"test:stream:{__name__}"
        group = f"test-group:{__name__}"
        await ensure_group(redis_conn, stream, group)
        groups = await redis_conn.xinfo_groups(stream)
        assert any(g["name"] == group for g in groups)
        await redis_conn.delete(stream)

    async def test_pending_depth_on_empty_stream(self, redis_conn):
        depth = await pending_depth(redis_conn, "nonexistent:stream")
        assert depth == 0

    async def test_xadd_and_read(self, redis_conn):
        stream = f"test:read:{__name__}"
        group = f"test-reader:{__name__}"
        await ensure_group(redis_conn, stream, group)
        await redis_conn.xadd(stream, {"key": "value"})
        rows = await read_group(redis_conn, stream, group, "consumer-1", count=1, block_ms=1000)
        found = False
        for s, messages in rows:
            if s == stream:
                for msg_id, data in messages:
                    if data.get("key") == "value":
                        found = True
                    await redis_conn.xack(stream, group, msg_id)
        assert found
        await redis_conn.delete(stream)

    async def test_multiple_messages(self, redis_conn):
        stream = f"test:multi:{__name__}"
        group = f"test-multi:{__name__}"
        await ensure_group(redis_conn, stream, group)
        for i in range(3):
            await redis_conn.xadd(stream, {"index": str(i)})
        depth = await pending_depth(redis_conn, stream)
        assert depth >= 3
        await redis_conn.delete(stream)
