from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger("downloader")

YT_DLP_PATH = str(Path(__file__).resolve().parent.parent.parent / "venv" / "bin" / "yt-dlp")

DOWNLOAD_TMP_DIR = Path("/tmp/tgbot_downloads")
MAX_UPLOAD_MB = 1900
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024


def _base_cmd() -> list[str]:
    cmd = [YT_DLP_PATH, "--quiet", "--no-warnings", "--no-playlist"]
    cmd.extend(["--remote-components", "ejs:github", "--js-runtimes", "node"])
    cmd.extend(["--format-sort", "+ext:mp4:m4a:avc1"])
    cookies = os.getenv("COOKIES_FILE", "")
    if cookies:
        cmd.extend(["--cookies", cookies])
    return cmd


async def probe_video(url: str) -> dict | None:
    """Fetch video metadata and available formats via yt-dlp."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *_base_cmd(),
            "--dump-json",
            "--skip-download",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            asyncio.gather(proc.stdout.read(), proc.stderr.read()),
            timeout=25,
        )
        if proc.returncode and proc.returncode != 0:
            err = stderr.decode(errors="replace")[:500]
            log.warning("probe failed for %s: %s", url, err)
            return None
        result = json.loads(stdout.decode(errors="replace"))
        return result
    except asyncio.TimeoutError:
        log.warning("probe timed out for %s", url)
        return None
    except (json.JSONDecodeError, Exception) as exc:
        log.warning("probe error for %s: %s", url, exc)
        return None


AUDIO_BITRATES = {"64k": 64, "128k": 128, "192k": 192, "best": 9999}


def format_selector(ctx: dict) -> str:
    """Build yt-dlp format string respecting the size limit."""
    quality = ctx.get("quality", "best")
    if quality.startswith("audio:"):
        bitrate = quality.split(":", 1)[1]
        limit = AUDIO_BITRATES.get(bitrate, 128)
        if limit >= 9999:
            return "bestaudio/best"
        return f"bestaudio[abr<={limit}]/bestaudio/best"
    if quality == "best":
        return f"bestvideo[filesize<{MAX_UPLOAD_BYTES}]+bestaudio/best[filesize<{MAX_UPLOAD_BYTES}]/best"
    if quality.endswith("p"):
        height = quality.replace("p", "")
        return (
            f"bestvideo[height<={height}][filesize<{MAX_UPLOAD_BYTES}]+"
            f"bestaudio/best[height<={height}][filesize<{MAX_UPLOAD_BYTES}]/best"
        )
    return "best"


async def download_video(url: str, quality: str, job_id: str) -> Path | None:
    """Download video using yt-dlp. Returns path to downloaded file or None."""
    DOWNLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)

    outtmpl = str(DOWNLOAD_TMP_DIR / f"{job_id}.%(ext)s")

    fmt = format_selector({"quality": quality})

    cmd = [
        *_base_cmd(),
        "--restrict-filenames",
        "-f", fmt,
        "-o", outtmpl,
        "--max-filesize", f"{MAX_UPLOAD_MB}M",
        "--retries", "5",
        "--fragment-retries", "5",
        url,
    ]
    if quality.startswith("audio"):
        cmd.extend(["-x", "--audio-format", "mp3"])
        if ":" in quality:
            bitrate = quality.split(":", 1)[1].replace("k", "")
            if bitrate.isdigit():
                cmd.extend(["--audio-quality", bitrate])
    else:
        cmd.extend(["--merge-output-format", "mp4"])

    log.info("downloading job=%s quality=%s url=%s", job_id, quality, url)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[:1000]
        log.warning("download failed job=%s: %s", job_id, err)
        return None

    # find the downloaded file
    for f in DOWNLOAD_TMP_DIR.iterdir():
        if f.name.startswith(job_id) and f.is_file():
            return f
    return None


async def download_instagram(url: str, job_id: str) -> list[Path] | None:
    """Download Instagram content using yt-dlp.

    Returns list of downloaded file paths, or None on failure.
    Can return multiple files (carousel posts with images/videos).
    """
    DOWNLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    job_dir = DOWNLOAD_TMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    outtmpl = str(job_dir / "%(id)s.%(ext)s")

    cmd = [
        YT_DLP_PATH, "--quiet", "--no-warnings",
        "--remote-components", "ejs:github", "--js-runtimes", "node",
        "-f", "best",
        "-o", outtmpl,
        "--max-filesize", f"{MAX_UPLOAD_MB}M",
        "--retries", "5",
        "--fragment-retries", "5",
        url,
    ]
    cookies = os.getenv("COOKIES_FILE", "")
    if cookies:
        cmd.extend(["--cookies", cookies])

    log.info("instagram download job=%s url=%s", job_id, url)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    stderr_text = stderr.decode(errors="replace")
    log.debug("instagram job=%s stderr: %s", job_id, stderr_text[:500])

    if proc.returncode != 0:
        log.warning("instagram download failed job=%s: %s", job_id, stderr_text[:500])
        shutil.rmtree(job_dir, ignore_errors=True)
        return None

    files = sorted(job_dir.iterdir())
    files = [f for f in files if f.is_file()]
    if not files:
        log.warning("instagram job=%s: no files produced", job_id)
        shutil.rmtree(job_dir, ignore_errors=True)
        return None

    return files


async def download_twitter(url: str, job_id: str) -> list[Path] | None:
    """Download Twitter/X content using yt-dlp.

    Returns list of downloaded file paths, or None on failure.
    Can return multiple files (tweet with multiple images/videos).
    Reuses the same job-dir pattern as Instagram downloads.
    """
    DOWNLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    job_dir = DOWNLOAD_TMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    outtmpl = str(job_dir / "%(id)s.%(ext)s")

    cmd = [
        YT_DLP_PATH, "--quiet", "--no-warnings",
        "--remote-components", "ejs:github", "--js-runtimes", "node",
        "-f", "best",
        "-o", outtmpl,
        "--max-filesize", f"{MAX_UPLOAD_MB}M",
        "--retries", "5",
        "--fragment-retries", "5",
        url,
    ]
    cookies = os.getenv("COOKIES_FILE", "")
    if cookies:
        cmd.extend(["--cookies", cookies])

    log.info("twitter download job=%s url=%s", job_id, url)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    stderr_text = stderr.decode(errors="replace")
    log.debug("twitter job=%s stderr: %s", job_id, stderr_text[:500])

    if proc.returncode != 0:
        log.warning("twitter download failed job=%s: %s", job_id, stderr_text[:500])
        shutil.rmtree(job_dir, ignore_errors=True)
        return None

    files = sorted(job_dir.iterdir())
    files = [f for f in files if f.is_file()]
    if not files:
        log.warning("twitter job=%s: no files produced", job_id)
        shutil.rmtree(job_dir, ignore_errors=True)
        return None

    return files


def cleanup_orphans(max_age_secs: int = 3600) -> None:
    """Remove orphaned temp files and job directories older than max_age_secs."""
    if not DOWNLOAD_TMP_DIR.exists():
        return
    now = __import__("time").time()
    for entry in DOWNLOAD_TMP_DIR.iterdir():
        if entry.is_file():
            # Legacy flat files from yt-dlp
            age = now - entry.stat().st_mtime
            if age > max_age_secs:
                entry.unlink(missing_ok=True)
                log.info("cleaned orphaned temp file: %s", entry.name)
        elif entry.is_dir():
            # Job directories from Instagram/yt-dlp downloads
            age = now - entry.stat().st_mtime
            if age > max_age_secs:
                shutil.rmtree(entry, ignore_errors=True)
                log.info("cleaned orphaned job dir: %s", entry.name)
