from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import fitz
from reportlab.lib.pagesizes import portrait
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .languages import is_rtl

try:
    from weasyprint import HTML, CSS
    from weasyprint.text.fonts import FontConfiguration
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False

try:
    from bidi.algorithm import get_display
    BIDI_AVAILABLE = True
except ImportError:
    BIDI_AVAILABLE = False


SCRIPT_FONT_MAP = {
    "Farsi": ["Vazirmatn-Regular.ttf", "NotoSansArabic-Regular.ttf", "NotoSans-Regular.ttf"],
    "Persian": ["Vazirmatn-Regular.ttf", "NotoSansArabic-Regular.ttf", "NotoSans-Regular.ttf"],
    "Arabic": ["NotoSansArabic-Regular.ttf", "Vazirmatn-Regular.ttf", "NotoSans-Regular.ttf"],
    "Urdu": ["NotoNastaliqUrdu-Regular.ttf", "Vazirmatn-Regular.ttf", "NotoSans-Regular.ttf"],
    "Hebrew": ["NotoSansHebrew.ttf", "NotoSans-Regular.ttf"],
    "Chinese": ["NotoSansCJK-Regular.ttc", "NotoSans-Regular.ttf"],
    "Japanese": ["NotoSansCJK-Regular.ttc", "NotoSans-Regular.ttf"],
    "Korean": ["NotoSansCJK-Regular.ttc", "NotoSans-Regular.ttf"],
    "Hindi": ["NotoSansDevanagari.ttf", "NotoSans-Regular.ttf"],
    "Russian": ["NotoSans-Regular.ttf"],
    "default": ["NotoSans-Regular.ttf", "Vazirmatn-Regular.ttf", "NotoSansArabic-Regular.ttf"],
}

SCRIPT_REQUIREMENTS = {
    "Farsi": "arabic",
    "Persian": "arabic",
    "Arabic": "arabic",
    "Urdu": "arabic",
    "Hebrew": "hebrew",
    "Chinese": "cjk",
    "Japanese": "cjk",
    "Korean": "cjk",
    "Russian": "cyrillic",
    "Hindi": "devanagari",
}

FONT_CSS_NAMES = {
    "Vazirmatn-Regular.ttf": "Vazirmatn",
    "Vazirmatn-Bold.ttf": "Vazirmatn",
    "NotoSans-Regular.ttf": "NotoSans",
    "NotoSansArabic-Regular.ttf": "NotoSansArabic",
    "NotoNastaliqUrdu-Regular.ttf": "NotoNastaliqUrdu",
    "NotoSansCJK-Regular.ttc": "NotoSansCJK",
    "NotoSansDevanagari.ttf": "NotoSansDevanagari",
    "NotoSansHebrew.ttf": "NotoSansHebrew",
    "NotoSansSC.ttf": "NotoSansSC",
}


def _rgb(color: int) -> tuple[float, float, float]:
    return (((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255)


# Font buffer cache: {fontname: font_bytes}
_font_buffer_cache: dict[str, bytes] = {}


def _get_font_for_script(target_language: str, font_dir: Path, page: fitz.Page, block_font_meta: dict[str, Any] | None = None, font_cache: set[str] | None = None) -> str:
    """Hybrid font selection: reuse embedded -> use bundled font via PyMuPDF."""
    if font_cache is None:
        font_cache = set()

    if block_font_meta and block_font_meta.get("embedded"):
        required_script = SCRIPT_REQUIREMENTS.get(target_language, "latin")
        if block_font_meta.get("glyph_coverage", {}).get(required_script, False):
            return block_font_meta["name"]

    candidates = SCRIPT_FONT_MAP.get(target_language, SCRIPT_FONT_MAP["default"])
    for filename in candidates:
        path = font_dir / filename
        if path.exists():
            fontname = f"F{Path(filename).stem.replace('-', '')}"
            try:
                page.insert_font(fontname=fontname, fontfile=str(path))
            except Exception:
                continue
            return fontname

    return "helv"


def _fit_text_in_rect(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    fontname: str,
    max_size: float,
    min_size: float,
    color: tuple[float, float, float],
) -> tuple[bool, float]:
    """Binary search for optimal font size that fits in rect."""
    lo, hi = min_size, max_size
    best_size = min_size
    best_fit = False

    while lo <= hi + 0.1:
        mid = (lo + hi) / 2
        rc = page.insert_textbox(rect, text, fontsize=mid, fontname=fontname, color=color)
        if rc >= 0:
            best_fit = True
            best_size = mid
            lo = mid + 0.5
        else:
            hi = mid - 0.5

    return best_fit, best_size


def render_final(job_dir: Path, target_language: str, font_dir: Path) -> tuple[Path, int]:
    profile = json.loads((job_dir / "style_profile.json").read_text(encoding="utf-8"))
    translations = json.loads((job_dir / "translations.json").read_text(encoding="utf-8"))
    mode = profile.get("mode", "A")
    if mode == "B":
        if WEASYPRINT_AVAILABLE:
            return render_mode_b_weasyprint(job_dir, profile, translations, target_language, font_dir), 0
        else:
            return render_mode_b_reportlab(job_dir, profile, translations, target_language, font_dir), 0
    pdf_path, fallback_pages = render_mode_a(job_dir, profile, translations, target_language, font_dir)
    if fallback_pages > 0 and WEASYPRINT_AVAILABLE:
        return render_mode_b_weasyprint(job_dir, profile, translations, target_language, font_dir), fallback_pages
    return pdf_path, fallback_pages


def render_mode_a(job_dir: Path, profile: dict, translations: dict, target_language: str, font_dir: Path) -> tuple[Path, int]:
    src = job_dir / "source.pdf"
    out = job_dir / f"translated_{target_language}.pdf"
    doc = fitz.open(src)
    fallback_pages = 0
    fallback_page_ids: set[int] = set()
    font_cache: set[str] = set()

    by_page: dict[int, list[str]] = {}
    for chunk in translations.values():
        by_page.setdefault(int(chunk["page_id"]), []).append(chunk["translated_text"])

    for page_id, texts in by_page.items():
        page = doc[page_id]
        page_blocks = profile["pages"][page_id]["blocks"]
        page_fallback = False

        if len(texts) == 1 and len(page_blocks) > 1:
            combined_rect = fitz.Rect(page_blocks[0]["bbox"])
            for b in page_blocks[1:]:
                combined_rect.include_rect(fitz.Rect(b["bbox"]))
            page.add_redact_annot(combined_rect, fill=(1, 1, 1))
            page.apply_redactions()
            block = page_blocks[0]
            original_size = float(block.get("size", 11))
            min_size = max(original_size * 0.55, 6.0)
            color = _rgb(block.get("color", 0))
            fontname = _get_font_for_script(target_language, font_dir, page, block.get("font_meta"), font_cache)
            fits, _ = _fit_text_in_rect(page, combined_rect, texts[0], fontname, original_size, min_size, color)
            if not fits:
                page_fallback = True
                page.insert_textbox(combined_rect, texts[0], fontsize=min_size, fontname=fontname, color=color)
        else:
            for block, text in zip(page_blocks, texts):
                rect = fitz.Rect(block["bbox"])
                page.add_redact_annot(rect, fill=(1, 1, 1))
                page.apply_redactions()
                original_size = float(block.get("size", 11))
                min_size = max(original_size * 0.55, 6.0)
                color = _rgb(block.get("color", 0))
                fontname = _get_font_for_script(target_language, font_dir, page, block.get("font_meta"), font_cache)
                fits, _ = _fit_text_in_rect(page, rect, text, fontname, original_size, min_size, color)
                if not fits:
                    page_fallback = True
                    page.insert_textbox(rect, text, fontsize=min_size, fontname=fontname, color=color)

        if page_fallback:
            fallback_pages += 1
            fallback_page_ids.add(page_id)

    profile["fallback_pages"] = list(fallback_page_ids)
    profile["fallback_count"] = fallback_pages
    write_profile(job_dir, profile)

    doc.save(out, deflate=True, garbage=4)
    doc.close()
    return out, fallback_pages


def write_profile(job_dir: Path, profile: dict[str, Any]) -> Path:
    path = job_dir / "style_profile.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def render_mode_b_weasyprint(
    job_dir: Path,
    profile: dict,
    translations: dict,
    target_language: str,
    font_dir: Path
) -> Path:
    """WeasyPrint-based Mode B: full style-preserving regeneration with RTL/CJK support."""
    out = job_dir / f"translated_{target_language}.pdf"

    # Prepare font configuration
    font_config = FontConfiguration()
    font_faces = _build_font_faces(font_dir, target_language)

    # Extract and encode images
    image_data = _extract_images_base64(job_dir / "source.pdf", profile)

    # Build HTML content
    html_content = _build_html(profile, translations, target_language, image_data)

    # Build CSS
    css_content = _build_css(profile, target_language, font_dir, font_faces)

    # Render with WeasyPrint
    html = HTML(string=html_content, base_url=str(job_dir))
    html.write_pdf(str(out), font_config=font_config, stylesheets=[CSS(string=css_content)])

    return out


def _build_font_faces(font_dir: Path, target_language: str) -> list[dict]:
    """Build @font-face rules for WeasyPrint."""
    faces = []
    candidates = SCRIPT_FONT_MAP.get(target_language, SCRIPT_FONT_MAP["default"])
    for filename in candidates:
        path = font_dir / filename
        if path.exists():
            css_name = FONT_CSS_NAMES.get(filename, filename.replace(".ttf", "").replace(".otf", "").replace(".ttc", ""))
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            ext = "truetype" if filename.endswith(".ttf") else "opentype"
            faces.append({
                "family": css_name,
                "src": f"data:font/{ext};base64,{b64}",
                "weight": "normal",
                "style": "normal",
            })
    return faces


def _build_css(profile: dict, target_language: str, font_dir: Path, font_faces: list[dict]) -> str:
    """Generate CSS for WeasyPrint rendering."""
    rtl = is_rtl(target_language)
    cjk = target_language in ["Chinese", "Japanese", "Korean"]

    # Primary font family
    primary_font = font_faces[0]["family"] if font_faces else "sans-serif"
    fallback_fonts = ", ".join(f'"{f["family"]}"' for f in font_faces[1:]) + ", sans-serif"
    font_family = f'"{primary_font}", {fallback_fonts}'

    css_parts = []

    # @font-face rules
    for face in font_faces:
        css_parts.append(f"""
@font-face {{
    font-family: '{face['family']}';
    src: url({face['src']});
    font-weight: {face['weight']};
    font-style: {face['style']};
}}
""")

    # Page setup from first page
    first_page = profile["pages"][0]
    css_parts.append(f"""
@page {{
    size: {first_page['width']}pt {first_page['height']}pt;
    margin: 0;
}}
""")

    # Base styles
    css_parts.append(f"""
* {{
    box-sizing: border-box;
}}
body {{
    font-family: {font_family};
    font-size: 11pt;
    line-height: 1.5;
    margin: 0;
    padding: 0;
    direction: {'rtl' if rtl else 'ltr'};
    text-align: {'right' if rtl else 'left'};
}}
""")

    # Chapter heading style
    if profile.get("chapters"):
        # Analyze chapter styling from profile
        chapter_font_size = 14
        chapter_color = "#000000"
        chapter_font_weight = "bold"
        if profile["chapters"]:
            first_chapter = profile["chapters"][0]
            # We'd need to look up the block to get styling
            # For now use defaults

        css_parts.append(f"""
.chapter {{
    font-size: {chapter_font_size}pt;
    font-weight: {chapter_font_weight};
    color: {chapter_color};
    margin-top: 1.5em;
    margin-bottom: 0.8em;
    page-break-before: avoid;
}}
""")

    # Paragraph styles
    css_parts.append("""
.paragraph {
    margin: 0.5em 0;
    text-align: justify;
    text-justify: inter-word;
}
""")

    # Image styles
    css_parts.append("""
.image {
    position: absolute;
    border: none;
}
""")

    # RTL specific
    if rtl:
        css_parts.append("""
.rtl-text {
    direction: rtl;
    text-align: right;
    unicode-bidi: embed;
}
""")

    # CJK specific
    if cjk:
        css_parts.append("""
.cjk-text {
    font-family: "NotoSansCJK", sans-serif;
    word-break: keep-all;
    line-break: strict;
}
""")

    return "\n".join(css_parts)


def _build_html(profile: dict, translations: dict, target_language: str, image_data: dict) -> str:
    """Generate HTML content from translations and profile."""
    rtl = is_rtl(target_language)
    cjk = target_language in ["Chinese", "Japanese", "Korean"]

    # Organize translations by page
    by_page: dict[int, list[dict]] = {}
    for chunk in translations.values():
        page_id = int(chunk["page_id"])
        by_page.setdefault(page_id, []).append(chunk)

    # Sort chunks within each page by chunk_id
    for page_id in by_page:
        by_page[page_id].sort(key=lambda x: x["chunk_id"])

    html_parts = ["<!DOCTYPE html>", "<html", f' dir="{"rtl" if rtl else "ltr"}"', ">"]
    html_parts.append("<head><meta charset='utf-8'></head>")
    html_parts.append("<body>")

    # Chapter detection - map block_ids to chapter status
    chapter_blocks = set()
    if profile.get("chapters"):
        for ch in profile["chapters"]:
            chapter_blocks.add(ch["block_id"])

    for page in profile["pages"]:
        page_id = page["page_id"]
        page_width = page["width"]
        page_height = page["height"]

        html_parts.append(f'<div class="page" style="width:{page_width}pt;height:{page_height}pt;position:relative;page-break-after:always;">')

        # Add images for this page
        for img in page.get("images", []):
            img_id = img["image_id"]
            if img_id in image_data:
                bbox = img["bbox"]
                html_parts.append(f'''
<img class="image" src="data:image/png;base64,{image_data[img_id]}"
     style="left:{bbox[0]}pt;top:{bbox[1]}pt;width:{bbox[2]-bbox[0]}pt;height:{bbox[3]-bbox[1]}pt;" />
''')

        # Add text content
        chunks = by_page.get(page_id, [])
        for chunk in chunks:
            text = chunk["translated_text"]
            block_ids = chunk.get("block_ids", [])

            # Check if this chunk contains a chapter heading
            is_chapter = any(bid in chapter_blocks for bid in block_ids)

            # Process paragraphs
            paragraphs = text.split("\n\n")
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue

                classes = ["paragraph"]
                if is_chapter:
                    classes.append("chapter")
                if rtl:
                    classes.append("rtl-text")
                if cjk:
                    classes.append("cjk-text")

                class_attr = ' '.join(classes)
                html_parts.append(f'<p class="{class_attr}">{_escape_html(para)}</p>')

        html_parts.append("</div>")  # page

    html_parts.append("</body></html>")
    return "\n".join(html_parts)


def _extract_images_base64(source_pdf: Path, profile: dict) -> dict[str, str]:
    """Extract images from source PDF as base64."""
    images = {}
    doc = fitz.open(source_pdf)
    for page in profile["pages"]:
        page_obj = doc[page["page_id"]]
        for img in page.get("images", []):
            img_id = img["image_id"]
            if img_id not in images:
                # Find the image in the page
                for item in page_obj.get_images(full=True):
                    rects = page_obj.get_image_rects(item[0])
                    for rect in rects:
                        # Check if this rect matches
                        img_bbox = img["bbox"]
                        if abs(rect.x0 - img_bbox[0]) < 5 and abs(rect.y0 - img_bbox[1]) < 5:
                            try:
                                pix = page_obj.get_pixmap(clip=rect)
                                img_bytes = pix.tobytes("png")
                                images[img_id] = base64.b64encode(img_bytes).decode("ascii")
                                break
                            except Exception:
                                pass
    doc.close()
    return images


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;"))


def render_mode_b_reportlab(
    job_dir: Path,
    profile: dict,
    translations: dict,
    target_language: str,
    font_dir: Path
) -> Path:
    """Fallback ReportLab-based Mode B."""
    out = job_dir / f"translated_{target_language}.pdf"
    font_name = register_font(target_language, font_dir)
    first = profile["pages"][0]
    c = canvas.Canvas(str(out), pagesize=portrait((first["width"], first["height"])))
    text_items = [translations[k]["translated_text"] for k in sorted(translations, key=lambda x: int(x))]
    text = "\n\n".join(text_items)
    for page in profile["pages"]:
        c.setPageSize((page["width"], page["height"]))
        y = page["height"] - 54
        c.setFont(font_name, 11)
        for paragraph in text.split("\n\n"):
            lines = _wrap(paragraph, 90)
            for line in lines:
                if y < 54:
                    c.showPage()
                    c.setPageSize((page["width"], page["height"]))
                    c.setFont(font_name, 11)
                    y = page["height"] - 54
                if is_rtl(target_language):
                    c.drawRightString(page["width"] - 54, y, line)
                else:
                    c.drawString(54, y, line)
                y -= 15
            y -= 8
        c.showPage()
        break
    c.save()
    return out


def register_font(target_language: str, font_dir: Path) -> str:
    candidates = SCRIPT_FONT_MAP.get(target_language, SCRIPT_FONT_MAP["default"])
    for filename in candidates:
        path = font_dir / filename
        if path.exists():
            pdfmetrics.registerFont(TTFont("BookFont", str(path)))
            return "BookFont"
    return "Helvetica"


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        if sum(len(w) + 1 for w in current) + len(word) > width:
            lines.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines or [""]