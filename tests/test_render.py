from __future__ import annotations

import json
import shutil
from pathlib import Path

import fitz
import pytest

from ebook_translator.languages import is_rtl, mode_for
from ebook_translator.pdf import build_chunks, extract_profile, write_profile
from ebook_translator.render import (
    SCRIPT_FONT_MAP,
    _build_html,
    _escape_html,
    _extract_images_base64,
    _fit_text_in_rect,
    _get_font_for_script,
    render_mode_a,
    render_mode_b_weasyprint,
    render_final,
)

FONT_DIR = Path("/root/ebook-translator/fonts")


class TestModeFor:
    def test_mode_b_for_rtl(self):
        assert mode_for("English", "Arabic") == "B"
        assert mode_for("English", "Farsi") == "B"
        assert mode_for("English", "Hebrew") == "B"
        assert mode_for("English", "Urdu") == "B"

    def test_mode_b_for_cjk(self):
        assert mode_for("English", "Chinese") == "B"
        assert mode_for("English", "Japanese") == "B"
        assert mode_for("English", "Korean") == "B"

    def test_mode_a_for_same_script(self):
        assert mode_for("English", "German") == "A_with_B_fallback"
        assert mode_for("English", "Spanish") == "A_with_B_fallback"

    def test_is_rtl(self):
        assert is_rtl("Arabic")
        assert is_rtl("Farsi")
        assert is_rtl("Hebrew")
        assert is_rtl("Urdu")
        assert not is_rtl("English")
        assert not is_rtl("German")


class TestRenderModeA:
    def test_render_german(self, simple_pdf: Path, test_job_dir: Path):
        profile = extract_profile(simple_pdf, "German", "English")
        write_profile(test_job_dir, profile)
        chunks = build_chunks(profile)
        translations = {
            str(c["chunk_id"]): {
                "chunk_id": c["chunk_id"],
                "page_id": c["page_id"],
                "block_ids": c["block_ids"],
                "translated_text": c["text"] + " [DE]",
            }
            for c in chunks
        }
        (test_job_dir / "translations.json").write_text(
            json.dumps(translations, ensure_ascii=False), encoding="utf-8"
        )
        shutil.copy2(simple_pdf, test_job_dir / "source.pdf")
        out_path, fallback_pages = render_mode_a(test_job_dir, profile, translations, "German", FONT_DIR)
        assert out_path.exists()
        assert isinstance(fallback_pages, int)
        doc = fitz.open(out_path)
        assert doc.page_count > 0
        doc.close()

    def test_render_french(self, simple_pdf: Path, test_job_dir: Path):
        profile = extract_profile(simple_pdf, "French", "English")
        write_profile(test_job_dir, profile)
        chunks = build_chunks(profile)
        translations = {
            str(c["chunk_id"]): {
                "chunk_id": c["chunk_id"],
                "page_id": c["page_id"],
                "block_ids": c["block_ids"],
                "translated_text": c["text"] + " [FR]",
            }
            for c in chunks
        }
        (test_job_dir / "translations.json").write_text(
            json.dumps(translations, ensure_ascii=False), encoding="utf-8"
        )
        shutil.copy2(simple_pdf, test_job_dir / "source.pdf")
        out_path, fallback_pages = render_final(test_job_dir, "French", FONT_DIR)
        assert out_path.exists()
        doc = fitz.open(out_path)
        assert doc.page_count > 0
        doc.close()


class TestScriptFontMap:
    def test_farsi_fonts(self):
        fonts = SCRIPT_FONT_MAP["Farsi"]
        assert any("Vazirmatn" in f for f in fonts)

    def test_arabic_fonts(self):
        fonts = SCRIPT_FONT_MAP["Arabic"]
        assert any("Arabic" in f for f in fonts)

    def test_cjk_fonts(self):
        fonts = SCRIPT_FONT_MAP["Chinese"]
        assert any("CJK" in f for f in fonts)

    def test_default_fonts(self):
        fonts = SCRIPT_FONT_MAP["default"]
        assert any("NotoSans-Regular" in f for f in fonts)

    def test_all_fonts_exist_on_disk(self):
        for lang, fonts in SCRIPT_FONT_MAP.items():
            for font_file in fonts:
                path = FONT_DIR / font_file
                if not path.exists():
                    pytest.skip(f"Font not on disk: {font_file}")


class TestEscapeHtml:
    def test_escapes_ampersand(self):
        assert _escape_html("AT&T") == "AT&amp;T"

    def test_escapes_angle_brackets(self):
        assert _escape_html("<hello>") == "&lt;hello&gt;"

    def test_escapes_quotes(self):
        assert _escape_html('he said "hi"') == "he said &quot;hi&quot;"

    def test_passes_through_safe_text(self):
        text = "Hello, World!"
        assert _escape_html(text) == text


class TestBuildChunks:
    def test_chunks_created(self, english_chunks):
        assert len(english_chunks) > 0
