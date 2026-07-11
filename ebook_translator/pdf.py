from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import fitz

from .languages import mode_for

SENTENCE_END = re.compile(r"(?<=[.!?。؟؛])\s+")


def extract_profile(source_pdf: Path, target_language: str, source_language: str = "English") -> dict[str, Any]:
    doc = fitz.open(source_pdf)
    pages: list[dict[str, Any]] = []
    fonts: Counter[str] = Counter()
    sizes: Counter[float] = Counter()
    total_words = 0
    chapters: list[dict[str, Any]] = []
    font_metadata: dict[str, dict[str, Any]] = {}

    for page_index, page in enumerate(doc):
        page_dict = page.get_text("dict")
        blocks = []
        for block_index, block in enumerate(page_dict.get("blocks", [])):
            if block.get("type") != 0:
                continue
            spans = [span for line in block.get("lines", []) for span in line.get("spans", [])]
            text = "\n".join(
                "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                for line in block.get("lines", [])
            ).strip()
            if not text:
                continue
            font_name = spans[0].get("font", "Helvetica") if spans else "Helvetica"
            size = float(spans[0].get("size", 11)) if spans else 11.0
            color = int(spans[0].get("color", 0)) if spans else 0
            flags = int(spans[0].get("flags", 0)) if spans else 0
            fonts[font_name] += 1
            sizes[round(size, 1)] += 1
            total_words += len(text.split())
            block_id = f"p{page_index}_b{block_index}"

            # Get detailed font metadata
            if font_name not in font_metadata:
                font_metadata[font_name] = _extract_font_metadata(doc, font_name, spans[0] if spans else None)

            blocks.append(
                {
                    "block_id": block_id,
                    "bbox": list(block["bbox"]),
                    "font": font_name,
                    "size": size,
                    "color": color,
                    "flags": flags,
                    "font_meta": font_metadata[font_name],
                    "text": text,
                }
            )
        if blocks:
            body_size = sizes.most_common(1)[0][0]
            for block in blocks:
                if block["size"] >= body_size * 1.35 and len(block["text"].split()) <= 12:
                    chapters.append({"page": page_index, "text": block["text"], "block_id": block["block_id"]})
        pages.append(
            {
                "page_id": page_index,
                "width": page.rect.width,
                "height": page.rect.height,
                "blocks": blocks,
                "images": _extract_images(page),
            }
        )

    profile = {
        "source_language": source_language,
        "target_language": target_language,
        "mode": mode_for(source_language, target_language),
        "page_count": len(doc),
        "total_words": total_words,
        "fonts": dict(fonts),
        "sizes": dict(sizes),
        "font_metadata": font_metadata,
        "chapters": chapters,
        "pages": pages,
    }
    doc.close()
    return profile


def _extract_font_metadata(doc: fitz.Document, font_name: str, span: dict[str, Any] | None) -> dict[str, Any]:
    """Extract detailed font metadata including glyph coverage info."""
    meta = {
        "name": font_name,
        "flags": span.get("flags", 0) if span else 0,
        "is_bold": bool(span.get("flags", 0) & 16) if span else False,
        "is_italic": bool(span.get("flags", 0) & 2) if span else False,
        "is_serif": bool(span.get("flags", 0) & 1) if span else False,
        "is_monospace": bool(span.get("flags", 0) & 8) if span else False,
        "embedded": False,
        "font_bbox": None,
        "ascent": None,
        "descent": None,
        "cap_height": None,
        "glyph_coverage": {},
    }

    # Find the font in the document
    for page in doc:
        fonts = page.get_fonts(full=True)
        for font in fonts:
            if font[3] == font_name:  # font[3] is the font name
                meta["embedded"] = font[1] == "Type0" or font[1] == "TrueType" or font[1] == "Type1"
                if meta["embedded"] and font[0]:  # font[0] is xref
                    try:
                        font_obj = doc.extract_font(font[0])
                        if font_obj:
                            meta["font_bbox"] = list(font_obj.get("bbox", [0, 0, 0, 0]))
                            meta["ascent"] = font_obj.get("ascent")
                            meta["descent"] = font_obj.get("descent")
                            meta["cap_height"] = font_obj.get("cap_height")
                            # Check glyph coverage for common scripts
                            meta["glyph_coverage"] = _check_glyph_coverage(font_obj)
                    except Exception:
                        pass
                break
        if meta["embedded"]:
            break

    return meta


def _check_glyph_coverage(font_obj: dict[str, Any]) -> dict[str, bool]:
    """Check if font has glyphs for target scripts."""
    coverage = {
        "latin": False,
        "arabic": False,
        "cyrillic": False,
        "cjk": False,
        "devanagari": False,
        "hebrew": False,
    }
    # This is a simplified check - in production we'd check actual cmap
    font_name = font_obj.get("name", "").lower()
    if any(x in font_name for x in ["noto", "arial", "times", "helvetica", "roboto", "opensans", "source"]):
        coverage["latin"] = True
    if any(x in font_name for x in ["amiri", "arabic", "kufi", "naskh", "vazir"]):
        coverage["arabic"] = True
    if any(x in font_name for x in ["cjk", "sc", "jp", "kr", "hans", "hant", "noto sans sc", "noto sans jp", "noto sans kr"]):
        coverage["cjk"] = True
    if any(x in font_name for x in ["devanagari", "noto sans devanagari"]):
        coverage["devanagari"] = True
    if any(x in font_name for x in ["hebrew", "noto sans hebrew"]):
        coverage["hebrew"] = True
    if any(x in font_name for x in ["cyrillic", "noto sans cypriot", "noto sans"]):
        coverage["cyrillic"] = True
    return coverage


def _extract_images(page: fitz.Page) -> list[dict[str, Any]]:
    images = []
    for index, item in enumerate(page.get_images(full=True)):
        rects = page.get_image_rects(item[0])
        for rect in rects:
            images.append({"image_id": f"img_{index}", "bbox": list(rect)})
    return images


def write_profile(job_dir: Path, profile: dict[str, Any]) -> Path:
    path = job_dir / "style_profile.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_profile(job_dir: Path) -> dict[str, Any]:
    return json.loads((job_dir / "style_profile.json").read_text(encoding="utf-8"))


def build_chunks(profile: dict[str, Any], min_words: int = 120, max_words: int = 320) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    chunk_id = 0
    for page in profile["pages"]:
        pending: list[str] = []
        block_ids: list[str] = []
        word_count = 0
        for block in page["blocks"]:
            parts = split_paragraph(block["text"], max_words)
            for part in parts:
                words = len(part.split())
                if pending and word_count + words > max_words:
                    chunks.append(_chunk(profile, chunk_id, page["page_id"], block_ids, pending))
                    chunk_id += 1
                    pending, block_ids, word_count = [], [], 0
                pending.append(part)
                block_ids.append(block["block_id"])
                word_count += words
                if word_count >= min_words:
                    chunks.append(_chunk(profile, chunk_id, page["page_id"], block_ids, pending))
                    chunk_id += 1
                    pending, block_ids, word_count = [], [], 0
        if pending:
            chunks.append(_chunk(profile, chunk_id, page["page_id"], block_ids, pending))
            chunk_id += 1
    return chunks


def split_paragraph(text: str, max_words: int) -> list[str]:
    if not text or not text.strip():
        return []
    if len(text.split()) <= max_words:
        return [text]
    sentences = SENTENCE_END.split(text)
    parts: list[str] = []
    buf: list[str] = []
    count = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        words = len(sentence.split())
        if buf and count + words > max_words:
            parts.append(" ".join(buf))
            buf, count = [], 0
        buf.append(sentence)
        count += words
    if buf:
        parts.append(" ".join(buf))
    return parts


def _chunk(profile: dict[str, Any], chunk_id: int, page_id: int, block_ids: list[str], texts: list[str]) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "page_id": page_id,
        "block_ids": block_ids,
        "target_language": profile["target_language"],
        "text": "\n\n".join(texts),
    }