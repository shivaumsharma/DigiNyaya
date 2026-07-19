"""Unit tests for app.documents.extraction.

Native-PDF and PDF-type-detection tests need no external binary (PyMuPDF is
pure pip). OCR tests need the real Tesseract binary installed and are
skipped (not failed) when it isn't -- matching this codebase's existing
convention of gracefully degrading around external dependencies (see
app.llm's provider fallbacks) rather than hard-failing CI on a machine
without it.

Run with (from backend/):
    python -m unittest tests.test_document_extraction -v
"""
from __future__ import annotations

import io
import shutil
import sys
import unittest
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.documents import extraction  # noqa: E402

_TESSERACT_AVAILABLE = shutil.which("tesseract") is not None


def _native_pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    raw = doc.tobytes()
    doc.close()
    return raw


def _scanned_pdf_bytes(text: str) -> bytes:
    """A PDF whose only content is a rasterized image -- no text layer at
    all, a genuine synthetic "scanned" document."""
    image = Image.new("RGB", (600, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), text, fill="black")
    buf = io.BytesIO()
    image.save(buf, format="PNG")

    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(fitz.Rect(0, 0, 600, 200), stream=buf.getvalue())
    raw = doc.tobytes()
    doc.close()
    return raw


def _image_bytes(text: str) -> bytes:
    image = Image.new("RGB", (400, 150), "white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), text, fill="black")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


class TestDetectPdfType(unittest.TestCase):
    def test_native_text_pdf_detected_as_native(self):
        raw = _native_pdf_bytes("Contract dated 12/01/2024 for Rs. 50,000 between Alice and Bob.")
        self.assertEqual(extraction.detect_pdf_type(raw), "native")

    def test_image_only_pdf_detected_as_scanned(self):
        raw = _scanned_pdf_bytes("Receipt dated 15/02/2024")
        self.assertEqual(extraction.detect_pdf_type(raw), "scanned")


class TestExtractNativeText(unittest.TestCase):
    def test_extracts_embedded_text(self):
        raw = _native_pdf_bytes("Agreement dated 01/02/2024 for Rs. 50,000.")
        pages = extraction.extract_native_text(raw)
        self.assertEqual(len(pages), 1)
        self.assertIn("Agreement dated 01/02/2024", pages[0])


class TestRasterizePages(unittest.TestCase):
    def test_produces_one_image_per_page(self):
        raw = _scanned_pdf_bytes("Some text")
        images = extraction.rasterize_pages(raw)
        self.assertEqual(len(images), 1)
        self.assertGreater(images[0].width, 0)
        self.assertGreater(images[0].height, 0)


class TestNormalizeText(unittest.TestCase):
    def test_collapses_whitespace(self):
        self.assertEqual(extraction.normalize_text("hello    world  \n"), "hello world")

    def test_drops_noise_only_lines(self):
        text = "Real content line\n----\n***\nAnother real line"
        normalized = extraction.normalize_text(text)
        self.assertNotIn("----", normalized)
        self.assertNotIn("***", normalized)
        self.assertIn("Real content line", normalized)
        self.assertIn("Another real line", normalized)

    def test_empty_input_returns_empty_string(self):
        self.assertEqual(extraction.normalize_text(""), "")


class TestExtractDocumentNative(unittest.TestCase):
    def test_native_pdf_extracted_without_ocr(self):
        raw = _native_pdf_bytes("Invoice dated 01/02/2024 for Rs. 42,999.")
        result = extraction.extract_document(raw, "application/pdf")
        self.assertIsNone(result.error)
        self.assertFalse(result.is_scanned)
        self.assertEqual(result.engine, "native")
        self.assertIsNone(result.ocr_confidence)
        self.assertIn("Invoice dated 01/02/2024", result.cleaned_text)


@unittest.skipUnless(_TESSERACT_AVAILABLE, "Tesseract binary not installed on this machine")
class TestExtractDocumentOCR(unittest.TestCase):
    def test_scanned_pdf_extracted_via_ocr(self):
        raw = _scanned_pdf_bytes("Receipt Rs 12000")
        result = extraction.extract_document(raw, "application/pdf")
        self.assertIsNone(result.error)
        self.assertTrue(result.is_scanned)
        self.assertEqual(result.engine, "tesseract")
        self.assertIsNotNone(result.ocr_confidence)
        self.assertGreaterEqual(result.ocr_confidence, 0.0)
        self.assertLessEqual(result.ocr_confidence, 1.0)

    def test_standalone_image_extracted_via_ocr(self):
        raw = _image_bytes("Contract Rs 50000")
        result = extraction.extract_document(raw, "image/png")
        self.assertIsNone(result.error)
        self.assertTrue(result.is_scanned)
        self.assertEqual(result.engine, "tesseract")


class TestExtractDocumentGracefulFailure(unittest.TestCase):
    def test_ocr_unavailable_reports_error_without_raising(self):
        if _TESSERACT_AVAILABLE:
            self.skipTest("Tesseract IS installed on this machine -- this test needs it absent")
        raw = _scanned_pdf_bytes("Some text")
        result = extraction.extract_document(raw, "application/pdf")
        self.assertIsNotNone(result.error)
        self.assertEqual(result.raw_text, "")


if __name__ == "__main__":
    unittest.main()
