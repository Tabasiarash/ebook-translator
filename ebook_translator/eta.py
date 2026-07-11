from __future__ import annotations

from .config import STREAM_TRANSLATE
from .providers import effective_rpm, load_provider_keys_async
from .queue import pending_depth


async def estimate_eta(r, providers_path, total_words: int) -> tuple[int, str]:
    estimated_chunks = max(1, int(total_words / 250))
    queue_depth = await pending_depth(r, STREAM_TRANSLATE)
    rpm = await effective_rpm(r, await load_provider_keys_async(providers_path))
    minutes = max(1, int((estimated_chunks + queue_depth) / rpm))
    if minutes < 60:
        return minutes, f"~{minutes} minutes"
    if minutes < 1440:
        hours = max(1, round(minutes / 60))
        return minutes, f"~{hours} hours, check back later today"
    days = max(1, round(minutes / 1440))
    return minutes, f"~{days} days - this is a long book or the queue is busy. I'll message you when it is ready."
