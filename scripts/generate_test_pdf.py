from __future__ import annotations

from pathlib import Path

import fitz


def generate_test_pdf(output: Path, num_pages: int = 5, with_images: bool = True) -> Path:
    doc = fitz.open()
    page_width, page_height = 595, 842  # A4

    for i in range(num_pages):
        page = doc.new_page(width=page_width, height=page_height)

        # Chapter heading on page 0
        if i == 0:
            page.insert_text(
                fitz.Point(72, 72),
                "Chapter 1: The Beginning",
                fontsize=18,
                fontname="helv",
                color=(0, 0, 0),
            )
            body_y = 120
        else:
            body_y = 72

        page.insert_text(
            fitz.Point(72, body_y),
            f"This is page {i + 1} of the test document. "
            "It contains sample text in English for testing the PDF translator. "
            "The quick brown fox jumps over the lazy dog. "
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
            "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. "
            "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. "
            "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.\n\n"
            "Special terms: The Quantum Engine, Dr. Archimedes Q. Wigglesworth of the Wigglesworth Corporation, "
            "Project Stardust, the Zephyr Protocol, and the floating city of Aeroville are all important proper nouns "
            "that should be translated consistently throughout the book.\n\n"
            "Paragraph two continues with more discussion of the Wigglesworth Corporation's proprietary Quantum Engine. "
            "Dr. Wigglesworth himself invented the Zephyr Protocol during his time at Aeroville. "
            "These terms will test glossary consistency across multiple chunks.",
            fontsize=11,
            fontname="helv",
            color=(0, 0, 0),
        )

        if with_images and i == 0:
            # Draw a simple rectangle as a fake image placeholder
            page.draw_rect(fitz.Rect(400, 200, 540, 340), color=(0, 0, 1), width=2)
            page.insert_text(
                fitz.Point(420, 270), "[Fake Image]", fontsize=10, fontname="helv", color=(0, 0, 1)
            )

    doc.save(str(output), deflate=True, garbage=4)
    doc.close()
    return output


def generate_test_pdf_multi_chapter(output: Path, num_chapters: int = 3, pages_per_chapter: int = 3) -> Path:
    doc = fitz.open()
    page_width, page_height = 595, 842

    for ch in range(num_chapters):
        for p in range(pages_per_chapter):
            page = doc.new_page(width=page_width, height=page_height)
            if p == 0:
                page.insert_text(
                    fitz.Point(72, 72),
                    f"Chapter {ch + 1}: The {chr(65 + ch)} Theme",
                    fontsize=18,
                    fontname="helv",
                    color=(0, 0, 0),
                )
                body_y = 120
            else:
                body_y = 72

            page.insert_text(
                fitz.Point(72, body_y),
                f"Content from chapter {ch + 1} page {p + 1}. "
                "This tests multi-chapter PDF ingestion. "
                "Dr. Wigglesworth and the Quantum Engine appear throughout. "
                "Aeroville is mentioned repeatedly to verify glossary extraction. "
                "The Zephyr Protocol is a key invented term that must be preserved. "
                "Project Stardust continues to drive the narrative forward.",
                fontsize=11,
                fontname="helv",
                color=(0, 0, 0),
            )

    doc.save(str(output), deflate=True, garbage=4)
    doc.close()
    return output


if __name__ == "__main__":
    out_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    out_dir.mkdir(parents=True, exist_ok=True)
    generate_test_pdf(out_dir / "test_simple.pdf", num_pages=5)
    generate_test_pdf_multi_chapter(out_dir / "test_multi_chapter.pdf", num_chapters=3, pages_per_chapter=3)
    print(f"Test PDFs generated in {out_dir}")
