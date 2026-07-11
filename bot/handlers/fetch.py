from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket as pysocket
from urllib.parse import urlparse

import aiohttp
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from ebook_translator.config import settings as get_settings
from ebook_translator.queue import connect

cfg = get_settings()
log = logging.getLogger("fetch_handler")

STREAM_FETCH = "fetch:pending"

URL_REGEX = re.compile(r"https?://[^\s]+")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
)

FETCH_TIMEOUT = aiohttp.ClientTimeout(total=cfg.fetch_timeout)

PRIVATE_RANGES = [
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "::1/128",
    "fc00::/7",
]


def _is_unsafe_ip(host: str) -> bool:
    try:
        addrs = set()
        for info in pysocket.getaddrinfo(host, 80, family=pysocket.AF_UNSPEC, type=pysocket.SOCK_STREAM):
            addr = info[4][0]
            addrs.add(addr)
        for addr in addrs:
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return True
            for cidr in PRIVATE_RANGES:
                if ip in ipaddress.ip_network(cidr):
                    return True
        return False
    except (pysocket.gaierror, ValueError, OSError):
        return True


async def _ssrf_safe_session() -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(
        family=0,
        ssl=False,
        force_close=True,
        limit_per_host=1,
    )
    return aiohttp.ClientSession(
        connector=connector,
        timeout=FETCH_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        max_redirects=0,
    )


async def _follow_and_fetch(
    session: aiohttp.ClientSession, url: str, max_redirects: int = 5
) -> aiohttp.ClientResponse | None:
    seen = set()
    current = url
    for _ in range(max_redirects + 2):
        host = urlparse(current).hostname
        if host and _is_unsafe_ip(host):
            log.warning("blocked unsafe host at redirect hop: %s", host)
            return None
        try:
            resp = await session.get(current, allow_redirects=False)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            log.warning("fetch failed at %s: %s", current, exc)
            return None
        if resp.status in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "")
            if not location or location in seen:
                return None
            seen.add(location)
            current = location if location.startswith("http") else urlparse(current)._replace(path=location).geturl()
            resp.close()
            continue
        return resp
    return None


async def _check_content_length(resp: aiohttp.ClientResponse) -> bool:
    cl = resp.headers.get("Content-Length")
    if cl:
        try:
            return int(cl) <= cfg.fetch_max_bytes
        except ValueError:
            pass
    return True


async def fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: `/fetch <url>`")
        return

    url = context.args[0].strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        await update.message.reply_text("Only `http://` and `https://` URLs are supported.")
        return

    host = parsed.hostname
    if not host:
        await update.message.reply_text("Invalid URL.")
        return

    if host.lower() in ("localhost", "127.0.0.1", "::1") or host.startswith("169.254"):
        await update.message.reply_text("This target is not allowed.")
        return

    if _is_unsafe_ip(host):
        await update.message.reply_text("This target resolves to a private/internal network address.")
        return

    rate_key = f"fetch:cooldown:{update.effective_user.id}"
    r = await connect(cfg.redis_url)
    last = await r.get(rate_key)
    if last:
        remaining = cfg.fetch_per_user_cooldown - (int(asyncio.get_event_loop().time()) - int(last))
        if remaining > 0:
            await update.message.reply_text(f"Please wait {int(remaining)}s before another fetch.")
            return

    await r.xadd(
        STREAM_FETCH,
        {
            "chat_id": str(update.effective_chat.id),
            "user_id": str(update.effective_user.id),
            "url": url,
        },
    )
    await r.setex(rate_key, cfg.fetch_per_user_cooldown, str(int(asyncio.get_event_loop().time())))
    await update.message.reply_text("Fetch queued. You'll receive the result shortly.")


HANDLERS = [
    CommandHandler("fetch", fetch),
]
