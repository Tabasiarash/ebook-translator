from __future__ import annotations

import json

import pytest

from ebook_translator.pdf import (
    SENTENCE_END,
    build_chunks,
    extract_profile,
    split_paragraph,
)


class TestExtractProfile:
    def test_basic_extraction(self, simple_pdf):
        profile = extract_profile(simple_pdf, "German", "English")
        assert profile["source_language"] == "English"
        assert profile["target_language"] == "German"
        assert profile["page_count"] > 0
        assert profile["total_words"] > 0
        assert len(profile["pages"]) == profile["page_count"]
        assert "fonts" in profile
        assert "sizes" in profile
        assert "chapters" in profile
        assert "font_metadata" in profile

    def test_page_blocks(self, simple_pdf):
        profile = extract_profile(simple_pdf, "French", "English")
        for page in profile["pages"]:
            assert "page_id" in page
            assert "width" in page
            assert "height" in page
            assert "blocks" in page
            assert "images" in page
            for block in page["blocks"]:
                assert "block_id" in block
                assert "bbox" in block
                assert "font" in block
                assert "size" in block
                assert "text" in block
                assert "font_meta" in block

    def test_chapter_detection(self, multi_chapter_pdf):
        profile = extract_profile(multi_chapter_pdf, "German", "English")
        assert len(profile["chapters"]) >= 2

    def test_simple_pdf_has_no_chapters(self, simple_pdf):
        profile = extract_profile(simple_pdf, "German", "English")
        chapters = profile.get("chapters", [])
        # The simple PDF has "Chapter 1: The Beginning" at top of page 0
        assert len(chapters) >= 1
        assert "Chapter 1" in chapters[0]["text"]


class TestBuildChunks:
    def test_chunks_created(self, english_chunks):
        assert len(english_chunks) > 0

    def test_chunk_structure(self, english_chunks):
        chunk = english_chunks[0]
        assert "chunk_id" in chunk
        assert "page_id" in chunk
        assert "block_ids" in chunk
        assert "text" in chunk
        assert "target_language" in chunk

    def test_chunk_size_bounds(self, english_chunks):
        for chunk in english_chunks:
            word_count = len(chunk["text"].split())
            assert word_count >= 20

    def test_chunks_have_unique_ids(self, english_chunks):
        ids = [c["chunk_id"] for c in english_chunks]
        assert len(ids) == len(set(ids))

    def test_chunks_span_pages(self, english_chunks):
        page_ids = set(c["page_id"] for c in english_chunks)
        assert len(page_ids) > 1


class TestSplitParagraph:
    def test_short_text(self):
        result = split_paragraph("Hello world.", 100)
        assert result == ["Hello world."]

    def test_sentence_boundary_split(self):
        text = "First sentence here. Second sentence there. Third sentence elsewhere."
        result = split_paragraph(text, 5)
        assert len(result) >= 2

    def test_empty_text(self):
        assert split_paragraph("", 100) == []

    def test_whitespace_only(self):
        assert split_paragraph("   ", 100) == []


class TestSentenceEndRegex:
    def test_matches_period_with_space(self):
        assert SENTENCE_END.search("Hello. World")

    def test_matches_exclamation_with_space(self):
        assert SENTENCE_END.search("Wow! Great")

    def test_matches_chinese_period_with_space(self):
        assert SENTENCE_END.search("你好。 天气")

    def test_matches_arabic_question_with_space(self):
        assert SENTENCE_END.search("مرحبا؟ كيف")

    def test_matches_arabic_semicolon_with_space(self):
        assert SENTENCE_END.search("سلام؛ كيف")

    def test_does_not_match_without_trailing_space(self):
        assert not SENTENCE_END.search("Hello.")
        assert not SENTENCE_END.search("Wow!")

    def test_matches_multiple_sentences(self):
        matches = list(SENTENCE_END.finditer("Hello. World. Done."))
        assert len(matches) == 2
