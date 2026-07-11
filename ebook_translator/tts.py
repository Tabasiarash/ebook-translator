from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import edge_tts

TTS_VOICE_MAP: dict[str, str] = {
    "Farsi": "fa-IR-DilaraNeural",
    "Persian": "fa-IR-DilaraNeural",
    "Arabic": "ar-SA-ZariyahNeural",
    "Spanish": "es-ES-ElviraNeural",
    "French": "fr-FR-DeniseNeural",
    "German": "de-DE-KatjaNeural",
    "Portuguese": "pt-BR-FranciscaNeural",
    "Italian": "it-IT-ElsaNeural",
    "Russian": "ru-RU-SvetlanaNeural",
    "Turkish": "tr-TR-EmelNeural",
    "Chinese": "zh-CN-XiaoxiaoNeural",
    "Japanese": "ja-JP-NanamiNeural",
    "Korean": "ko-KR-SunHiNeural",
    "Hindi": "hi-IN-SwaraNeural",
    "Urdu": "ur-PK-UzmaNeural",
    "Hebrew": "he-IL-HilaNeural",
    "default": "en-US-AriaNeural",
}


def voice_for(language: str) -> str:
    return TTS_VOICE_MAP.get(language, TTS_VOICE_MAP["default"])


async def generate_page_audio(text: str, voice: str, output_path: Path) -> float:
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(output_path))
    return time.time()


async def generate_audiobook(
    job_dir: Path,
    job_id: str,
    target_language: str,
    db_path: Path,
    progress_callback: callable | None = None,
) -> dict[str, Any]:
    from ebook_translator import db

    voice = voice_for(target_language)
    audio_dir = job_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    chunks = await db.fetchall(
        db_path,
        "SELECT chunk_id, page_num, translated_text, source_text FROM chunks WHERE job_id=? ORDER BY chunk_id",
        (job_id,),
    )

    if not chunks:
        return {"error": "no chunks found"}

    segments: list[Path] = []
    total = len(chunks)

    for i, chunk in enumerate(chunks):
        text = (chunk.get("translated_text") or chunk.get("source_text") or "").strip()
        if not text:
            continue

        seg_path = audio_dir / f"seg_{chunk['chunk_id']:06d}.mp3"
        if not seg_path.exists():
            await generate_page_audio(text, voice, seg_path)
        segments.append(seg_path)

        if progress_callback:
            progress_callback(i + 1, total)

    combined = await _concatenate_mp3(segments, job_dir / f"{job_id}.mp3")
    return {
        "path": str(combined),
        "segments": len(segments),
        "duration_estimate": _estimate_duration(len(segments), chunks),
    }


async def _concatenate_mp3(segments: list[Path], output: Path) -> Path:
    if not segments:
        output.write_bytes(b"")
        return output

    data = bytearray()
    for seg in segments:
        data.extend(seg.read_bytes())
    output.write_bytes(bytes(data))
    return output


def _estimate_duration(segment_count: int, chunks: list[dict]) -> int:
    total_chars = sum(
        len(chunk.get("translated_text") or chunk.get("source_text") or "")
        for chunk in chunks
    )
    return max(1, int(total_chars / 15 / 60))
