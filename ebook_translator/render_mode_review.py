from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fitz

from . import db
from .languages import is_rtl
from .render import SCRIPT_FONT_MAP

REVIEW_COLOR = (0.29, 0.44, 0.65)
TOP_MARGIN = 36
BOTTOM_MARGIN = 36
GAP = 4
SEGMENT_MARGIN = 8
TRANS_FONTSIZE = 9.0


def _review_font_path(target_lang: str, font_dir: Path) -> str | None:
    candidates = SCRIPT_FONT_MAP.get(target_lang, SCRIPT_FONT_MAP["default"])
    for filename in candidates:
        path = font_dir / filename
        if path.exists():
            return str(path)
    return None


def _get_chunk_bbox(chunk: dict, profile_page: dict) -> fitz.Rect | None:
    block_ids = chunk["block_id"].split(",") if chunk.get("block_id") else []
    matching = [b for b in profile_page.get("blocks", []) if b["block_id"] in block_ids]
    if not matching:
        return None
    bbox = fitz.Rect(matching[0]["bbox"])
    for b in matching[1:]:
        bbox.include_rect(fitz.Rect(b["bbox"]))
    return bbox


async def render_review(job_dir: Path, job_id: str, target_lang: str, font_dir: Path, db_path: Path) -> Path:
    src = job_dir / "source.pdf"
    profile_path = job_dir / "style_profile.json"
    if not src.exists() or not profile_path.exists():
        raise FileNotFoundError("source.pdf or style_profile.json not found")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    out = job_dir / f"review_{target_lang}.pdf"

    chunks = await db.fetchall(
        db_path,
        "SELECT * FROM chunks WHERE job_id=? AND status='done' ORDER BY page_num, chunk_id",
        (job_id,),
    )
    chunks_by_page: dict[int, list[dict]] = {}
    for c in chunks:
        chunks_by_page.setdefault(c["page_num"], []).append(c)

    font_path = _review_font_path(target_lang, font_dir)
    rtl = is_rtl(target_lang)
    fontname = "helv"
    if font_path:
        fontname = f"Rev{Path(font_path).stem.replace('-', '')}"

    src_doc = fitz.open(src)
    out_doc = fitz.open()

    for page_idx, profile_page in enumerate(profile.get("pages", [])):
        if page_idx >= len(src_doc):
            break
        pw = profile_page.get("width", src_doc[page_idx].rect.width)
        ph = profile_page.get("height", src_doc[page_idx].rect.height)

        bands: list[tuple[float, str, Any]] = []
        for chunk in chunks_by_page.get(page_idx, []):
            bbox = _get_chunk_bbox(chunk, profile_page)
            if bbox is None:
                continue
            bands.append((bbox.y0, "text", {
                "bbox": bbox,
                "translated_text": chunk.get("translated_text") or chunk.get("source_text", ""),
            }))
        for img in profile_page.get("images", []):
            bands.append((img["bbox"][1], "image", {"bbox": fitz.Rect(img["bbox"])}))

        bands.sort(key=lambda x: x[0])

        current_page: fitz.Page | None = None
        cumulative_offset = 0.0

        for _, band_type, data in bands:
            bbox: fitz.Rect = data["bbox"]
            bbox_height = bbox.y1 - bbox.y0

            if current_page is None:
                current_page = out_doc.new_page(width=pw, height=ph)
                if font_path:
                    try:
                        current_page.insert_font(fontname=fontname, fontfile=font_path)
                    except Exception:
                        pass
                cumulative_offset = -bbox.y0 + TOP_MARGIN
                band_top = TOP_MARGIN
            else:
                band_top = bbox.y0 + cumulative_offset

            if band_top + bbox_height + GAP + 30 > ph - BOTTOM_MARGIN:
                current_page = out_doc.new_page(width=pw, height=ph)
                if font_path:
                    try:
                        current_page.insert_font(fontname=fontname, fontfile=font_path)
                    except Exception:
                        pass
                cumulative_offset = -bbox.y0 + TOP_MARGIN
                band_top = TOP_MARGIN

            target_rect = fitz.Rect(bbox.x0, band_top, bbox.x1, band_top + bbox_height)
            current_page.show_pdf_page(target_rect, src_doc, page_idx, clip=bbox)

            if band_type == "text":
                text = data["translated_text"]
                if font_path:
                    try:
                        current_page.insert_font(fontname=fontname, fontfile=font_path)
                    except Exception:
                        pass
                trans_y = target_rect.y1 + GAP
                avail_h = ph - BOTTOM_MARGIN - trans_y
                if avail_h < 20:
                    current_page = out_doc.new_page(width=pw, height=ph)
                    if font_path:
                        try:
                            current_page.insert_font(fontname=fontname, fontfile=font_path)
                        except Exception:
                            pass
                    trans_y = TOP_MARGIN
                    avail_h = ph - TOP_MARGIN - BOTTOM_MARGIN
                trans_rect = fitz.Rect(bbox.x0, trans_y, bbox.x1, trans_y + avail_h)
                result = current_page.insert_textbox(
                    trans_rect,
                    text,
                    fontsize=TRANS_FONTSIZE,
                    fontname=fontname,
                    color=REVIEW_COLOR,
                    align=fitz.TEXT_ALIGN_RIGHT if rtl else fitz.TEXT_ALIGN_LEFT,
                )
                actual_trans_height = result if result > 0 else avail_h
                cumulative_offset += bbox_height + GAP + actual_trans_height + SEGMENT_MARGIN
            else:
                cumulative_offset += bbox_height + SEGMENT_MARGIN

    src_doc.close()
    out_doc.save(out, deflate=True, garbage=4)
    out_doc.close()
    return out
