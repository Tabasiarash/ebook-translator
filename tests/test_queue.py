from __future__ import annotations

import pytest

from ebook_translator.queue import connect, ensure_group, pending_depth, read_group
from ebook_translator.config import STREAM_INGEST, GROUP_INGEST


class TestRedisStream:
    async def test_connect(self):
        r = await connect("redis://127.0.0.1:6379/0")
        assert await r.ping()

    async def test_ensure_group_creates_stream(self):
        r = await connect("redis://127.0.0.1:6379/0")
        stream = f"test:stream:{__name__}"
        group = f"test-group:{__name__}"
        await ensure_group(r, stream, group)
        groups = await r.xinfo_groups(stream)
        assert any(g["name"] == group for g in groups)
        await r.delete(stream)

    async def test_pending_depth_on_empty_stream(self):
        r = await connect("redis://127.0.0.1:6379/0")
        depth = await pending_depth(r, "nonexistent:stream")
        assert depth == 0

    async def test_xadd_and_read(self):
        r = await connect("redis://127.0.0.1:6379/0")
        stream = f"test:read:{__name__}"
        group = f"test-reader:{__name__}"
        await ensure_group(r, stream, group)
        await r.xadd(stream, {"key": "value"})
        rows = await read_group(r, stream, group, "consumer-1", count=1, block_ms=1000)
        found = False
        for s, messages in rows:
            if s == stream:
                for msg_id, data in messages:
                    if data.get("key") == "value":
                        found = True
                    await r.xack(stream, group, msg_id)
        assert found
        await r.delete(stream)

    async def test_multiple_messages(self):
        r = await connect("redis://127.0.0.1:6379/0")
        stream = f"test:multi:{__name__}"
        group = f"test-multi:{__name__}"
        await ensure_group(r, stream, group)
        for i in range(3):
            await r.xadd(stream, {"index": str(i)})
        depth = await pending_depth(r, stream)
        assert depth >= 3
        await r.delete(stream)
