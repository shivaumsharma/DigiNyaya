"""Unit tests for app.documents.extraction.

Native-PDF and PDF-type-detection tests need no external binary (PyMuPDF is
pure pip). OCR tests for the Tesseract *fallback* path need the real
Tesseract binary installed and are skipped (not failed) when it isn't --
matching this codebase's existing convention of gracefully degrading around
external dependencies (see app.llm's provider fallbacks) rather than
hard-failing CI on a machine without it.

Sarvam Document AI (the primary OCR path) is tested by monkeypatching
``extraction._sarvam_ocr`` rather than hitting the real network -- a live
API call in an automated unit test would be slow, flaky, cost credits, and
require a real key in CI. End-to-end verification against the real API was
done manually (see the OCR-swap session that added this path).

Run with (from backend/):
    python -m unittest tests.test_document_extraction -v
"""
from __future__ import annotations

import dataclasses
import io
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

import fitz
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.circuit_breaker import CircuitBreaker  # noqa: E402
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


class TestExtractDocumentOCRSarvamPrimary(unittest.TestCase):
    """Sarvam Document AI is tried first; _sarvam_ocr is mocked so these
    don't hit the real network."""

    def test_scanned_pdf_uses_sarvam_when_available(self):
        raw = _scanned_pdf_bytes("Receipt Rs 12000")
        with mock.patch.object(extraction, "_sarvam_ocr", return_value=("Receipt Rs 12000", 0.92)):
            result = extraction.extract_document(raw, "application/pdf")
        self.assertIsNone(result.error)
        self.assertTrue(result.is_scanned)
        self.assertEqual(result.engine, "sarvam_doc_ai")
        self.assertEqual(result.ocr_confidence, 0.92)

    def test_standalone_image_uses_sarvam_when_available(self):
        raw = _image_bytes("Contract Rs 50000")
        with mock.patch.object(extraction, "_sarvam_ocr", return_value=("Contract Rs 50000", 0.92)):
            result = extraction.extract_document(raw, "image/png")
        self.assertIsNone(result.error)
        self.assertTrue(result.is_scanned)
        self.assertEqual(result.engine, "sarvam_doc_ai")


@unittest.skipUnless(_TESSERACT_AVAILABLE, "Tesseract binary not installed on this machine")
class TestExtractDocumentOCRTesseractFallback(unittest.TestCase):
    """Tesseract is only reached when Sarvam is unconfigured/unavailable --
    forced here by mocking _sarvam_ocr to return None (its "unavailable"
    contract), independent of whether a real Sarvam key happens to be set
    in this environment."""

    def test_scanned_pdf_falls_back_to_tesseract(self):
        raw = _scanned_pdf_bytes("Receipt Rs 12000")
        with mock.patch.object(extraction, "_sarvam_ocr", return_value=None):
            result = extraction.extract_document(raw, "application/pdf")
        self.assertIsNone(result.error)
        self.assertTrue(result.is_scanned)
        self.assertEqual(result.engine, "tesseract")
        self.assertIsNotNone(result.ocr_confidence)
        self.assertGreaterEqual(result.ocr_confidence, 0.0)
        self.assertLessEqual(result.ocr_confidence, 1.0)

    def test_standalone_image_falls_back_to_tesseract(self):
        raw = _image_bytes("Contract Rs 50000")
        with mock.patch.object(extraction, "_sarvam_ocr", return_value=None):
            result = extraction.extract_document(raw, "image/png")
        self.assertIsNone(result.error)
        self.assertTrue(result.is_scanned)
        self.assertEqual(result.engine, "tesseract")


class TestExtractDocumentAudio(unittest.TestCase):
    """Audio evidence has no local fallback engine (unlike OCR's Tesseract
    fallback) -- _sarvam_transcribe is mocked so these don't hit the real
    network; live verification against the real API was done manually."""

    def test_audio_transcribed_via_sarvam(self):
        with mock.patch.object(
            extraction, "_sarvam_transcribe",
            return_value=("Speaker 0: I paid for the laptop.\nSpeaker 1: We will refund you.", 0.9),
        ):
            result = extraction.extract_document(b"RIFF....WAVEfmt ", "audio/wav")
        self.assertIsNone(result.error)
        self.assertTrue(result.is_scanned)
        self.assertEqual(result.engine, "sarvam_stt")
        self.assertEqual(result.ocr_confidence, 0.9)
        self.assertIn("Speaker 0", result.cleaned_text)
        self.assertIn("Speaker 1", result.cleaned_text)

    def test_audio_unavailable_reports_error_without_raising(self):
        with mock.patch.object(extraction, "_sarvam_transcribe", return_value=None):
            result = extraction.extract_document(b"RIFF....WAVEfmt ", "audio/wav")
        self.assertIsNotNone(result.error)
        self.assertEqual(result.raw_text, "")


class TestExtractDocumentGracefulFailure(unittest.TestCase):
    def test_ocr_unavailable_reports_error_without_raising(self):
        if _TESSERACT_AVAILABLE:
            self.skipTest("Tesseract IS installed on this machine -- this test needs it absent")
        raw = _scanned_pdf_bytes("Some text")
        with mock.patch.object(extraction, "_sarvam_ocr", return_value=None):
            result = extraction.extract_document(raw, "application/pdf")
        self.assertIsNotNone(result.error)
        self.assertEqual(result.raw_text, "")


class TestSarvamOcrCircuitBreaker(unittest.TestCase):
    """An open breaker must short-circuit _sarvam_ocr before it even
    imports/constructs the SarvamAI client -- proven by asserting the SDK
    constructor is never called, not just that the return value is None."""

    def setUp(self):
        self._breaker = CircuitBreaker(name="test_doc_ai")
        self._breaker_patch = mock.patch.object(extraction, "_ocr_breaker", self._breaker)
        self._breaker_patch.start()
        self._key_patch = mock.patch.object(
            extraction, "config", dataclasses.replace(extraction.config, sarvam_api_key="test-key")
        )
        self._key_patch.start()

    def tearDown(self):
        self._breaker_patch.stop()
        self._key_patch.stop()

    def test_open_breaker_skips_client_construction(self):
        for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
            self._breaker.record_failure()

        with mock.patch("sarvamai.SarvamAI") as mock_client_cls:
            result = extraction._sarvam_ocr(b"raw", "doc.pdf", "application/pdf", "en-IN")

        self.assertIsNone(result)
        mock_client_cls.assert_not_called()

    def test_closed_breaker_calls_client_and_records_success(self):
        mock_client = mock.MagicMock()
        mock_client.doc_ai.digitise.return_value = mock.MagicMock(status="completed", job_id="job-1")
        mock_client.doc_ai.get_results.return_value = mock.MagicMock(
            documents=[mock.MagicMock(pages=[mock.MagicMock(blocks=[{"text": "hello", "reading_order": 0}])])]
        )
        with mock.patch("sarvamai.SarvamAI", return_value=mock_client):
            result = extraction._sarvam_ocr(b"raw", "doc.pdf", "application/pdf", "en-IN")

        self.assertEqual(result, ("hello", 0.92))
        self.assertTrue(self._breaker.allow())

    def test_exception_records_failure(self):
        with mock.patch("sarvamai.SarvamAI", side_effect=RuntimeError("network down")):
            for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
                result = extraction._sarvam_ocr(b"raw", "doc.pdf", "application/pdf", "en-IN")
                self.assertIsNone(result)

        self.assertFalse(self._breaker.allow())


class TestSarvamSttCircuitBreaker(unittest.TestCase):
    def setUp(self):
        self._breaker = CircuitBreaker(name="test_stt")
        self._breaker_patch = mock.patch.object(extraction, "_stt_breaker", self._breaker)
        self._breaker_patch.start()
        self._key_patch = mock.patch.object(
            extraction, "config", dataclasses.replace(extraction.config, sarvam_api_key="test-key")
        )
        self._key_patch.start()

    def tearDown(self):
        self._breaker_patch.stop()
        self._key_patch.stop()

    def test_open_breaker_skips_client_construction(self):
        for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
            self._breaker.record_failure()

        with mock.patch("sarvamai.SarvamAI") as mock_client_cls:
            result = extraction._sarvam_transcribe(b"RIFF....WAVEfmt ", "audio/wav")

        self.assertIsNone(result)
        mock_client_cls.assert_not_called()

    def test_exception_records_failure(self):
        with mock.patch("sarvamai.SarvamAI", side_effect=RuntimeError("network down")):
            for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
                result = extraction._sarvam_transcribe(b"RIFF....WAVEfmt ", "audio/wav")
                self.assertIsNone(result)

        self.assertFalse(self._breaker.allow())


if __name__ == "__main__":
    unittest.main()
