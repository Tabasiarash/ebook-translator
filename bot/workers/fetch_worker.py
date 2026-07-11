from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket as pysocket
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import trafilatura
from telegram import Bot
from telegram.request import HTTPXRequest

from ebook_translator.config import settings
from ebook_translator.logging_config import configure_logging
from ebook_translator.queue import connect, ensure_group, read_group

cfg = settings()
log = logging.getLogger("fetch_worker")

STREAM_FETCH = "fetch:pending"
GROUP_FETCH = "ebook-fetch"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
)

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
            addrs.add(info[4][0])
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


async def main() -> None:
    log = configure_logging("ebook-fetch", cfg.log_dir)
    r = await connect(cfg.redis_url)
    await ensure_group(r, STREAM_FETCH, GROUP_FETCH)
    consumer = "fetch-worker"
    log.info("fetch worker started")

    req = HTTPXRequest(connection_pool_size=1)
    bot = Bot(token=cfg.telegram_bot_token, base_url=cfg.telegram_api_base_url + "/bot", request=req)

    connector = aiohttp.TCPConnector(
        family=0,
        ssl=False,
        force_close=True,
        limit_per_host=1,
    )

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=cfg.fetch_timeout),
        headers={"User-Agent": USER_AGENT},
    ) as session:
        while True:
            rows = await read_group(r, STREAM_FETCH, GROUP_FETCH, consumer)
            if not rows:
                await asyncio.sleep(1)
                continue

            for stream, messages in rows:
                for msg_id, data in messages:
                    chat_id = int(data["chat_id"])
                    url = data["url"]

                    try:
                        await _process_fetch(session, bot, r, stream, msg_id, chat_id, url)
                    except Exception as exc:
                        log.exception("fetch job failed: %s", exc)
                        await r.xack(stream, GROUP_FETCH, msg_id)


async def _process_fetch(
    session: aiohttp.ClientSession,
    bot: Bot,
    r,
    stream: str,
    msg_id: str,
    chat_id: int,
    url: str,
) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host or host.lower() in ("localhost", "127.0.0.1", "::1") or _is_unsafe_ip(host):
        await bot.send_message(chat_id, "Fetch rejected: unsafe target.")
        await r.xack(stream, GROUP_FETCH, msg_id)
        return

    resp = await _fetch_with_redirect_check(session, url)
    if resp is None:
        await bot.send_message(chat_id, "Fetch failed: could not reach the URL.")
        await r.xack(stream, GROUP_FETCH, msg_id)
        return

    async with resp:
        if resp.status >= 400:
            await bot.send_message(chat_id, f"Fetch failed: HTTP {resp.status}.")
            await r.xack(stream, GROUP_FETCH, msg_id)
            return

        cl = resp.headers.get("Content-Length")
        if cl and int(cl) > cfg.fetch_max_bytes:
            await bot.send_message(chat_id, f"Fetch aborted: content too large ({int(int(cl)/1024/1024)}MB > {cfg.fetch_max_mb}MB).")
            await r.xack(stream, GROUP_FETCH, msg_id)
            return

        content_type = (resp.headers.get("Content-Type") or "application/octet-stream").lower()
        disposition = resp.headers.get("Content-Disposition") or ""

        # Stream to temp file with size cap
        with tempfile.NamedTemporaryFile(delete=False, suffix=_suffix_from(content_type, disposition, url)) as tmp:
            tmp_path = Path(tmp.name)
            total = 0
            try:
                async for chunk in resp.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > cfg.fetch_max_bytes:
                        break
                    tmp.write(chunk)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                tmp_path.unlink(missing_ok=True)
                await bot.send_message(chat_id, f"Fetch failed during download: {exc}")
                await r.xack(stream, GROUP_FETCH, msg_id)
                return

        if total > cfg.fetch_max_bytes:
            tmp_path.unlink(missing_ok=True)
            await bot.send_message(chat_id, f"Fetch aborted: content exceeded {cfg.fetch_max_mb}MB limit.")
            await r.xack(stream, GROUP_FETCH, msg_id)
            return

    # Determine upload type
    is_html = "text/html" in content_type or url.endswith(".htm") or url.endswith(".html")
    is_image = content_type.startswith("image/")
    is_text = content_type.startswith("text/")

    if is_html and total > 0:
        # Extract readable text via trafilatura, send as text + attachment
        raw_html = tmp_path.read_text(errors="replace")
        extracted = trafilatura.extract(raw_html, include_links=True, output_format="text")
        if extracted and len(extracted) > 50:
            preview = extracted[:3000]
            await bot.send_message(chat_id, f"📄 Readable text:\n\n{preview}")
        with open(tmp_path, "rb") as f:
            await bot.send_document(chat_id, f, filename=tmp_path.name, caption="Raw HTML")
    elif is_image and total > 0:
        with open(tmp_path, "rb") as f:
            try:
                await bot.send_photo(chat_id, f)
            except Exception:
                with open(tmp_path, "rb") as f2:
                    await bot.send_document(chat_id, f2, filename=tmp_path.name)
    elif is_text and total > 0:
        text = tmp_path.read_text(errors="replace")[:4000]
        await bot.send_message(chat_id, f"📄 Content:\n\n{text}")
    elif total > 0:
        with open(tmp_path, "rb") as f:
            await bot.send_document(chat_id, f, filename=tmp_path.name)
    else:
        await bot.send_message(chat_id, "Fetch returned empty content.")

    tmp_path.unlink(missing_ok=True)
    await r.xack(stream, GROUP_FETCH, msg_id)


async def _fetch_with_redirect_check(session: aiohttp.ClientSession, url: str) -> aiohttp.ClientResponse | None:
    seen = set()
    current = url
    for _ in range(6):
        host = urlparse(current).hostname
        if host and _is_unsafe_ip(host):
            log.warning("blocked unsafe redirect hop: %s", host)
            return None
        try:
            resp = await session.get(current, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=cfg.fetch_timeout))
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            log.warning("fetch failed at %s: %s", current, exc)
            return None
        if resp.status in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "")
            if not location or location in seen:
                resp.close()
                return None
            seen.add(location)
            current = location if location.startswith("http") else urlparse(current)._replace(path=location).geturl()
            resp.close()
            continue
        return resp
    return None


def _suffix_from(content_type: str, disposition: str, url: str) -> str:
    if disposition and "filename=" in disposition:
        name = disposition.split("filename=")[-1].strip('"').strip("'")
        if "." in name:
            return "." + name.rsplit(".", 1)[-1]
    if "text/html" in content_type:
        return ".html"
    if "image/png" in content_type:
        return ".png"
    if "image/jpeg" in content_type or "image/jpg" in content_type:
        return ".jpg"
    if "image/gif" in content_type:
        return ".gif"
    if "image/webp" in content_type:
        return ".webp"
    if "application/pdf" in content_type:
        return ".pdf"
    if "application/zip" in content_type:
        return ".zip"
    path = urlparse(url).path
    if "." in path:
        ext = path.rsplit(".", 1)[-1]
        if len(ext) <= 5:
            return "." + ext
    return ".bin"


if __name__ == "__main__":
    asyncio.run(main())
