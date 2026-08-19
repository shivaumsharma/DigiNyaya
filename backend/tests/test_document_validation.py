"""Unit tests for app.documents.validation -- magic-byte MIME sniffing and
upload validation (size cap, extension/content-type mismatch detection).

Run with (from backend/):
    python -m unittest tests.test_document_validation -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.documents.validation import is_audio_mime_type, sniff_mime_type, validate_upload  # noqa: E402

_MAX = 15 * 1024 * 1024


class TestSniffMimeType(unittest.TestCase):
    def test_pdf_signature(self):
        self.assertEqual(sniff_mime_type(b"%PDF-1.4 rest of file"), "application/pdf")

    def test_jpeg_signature(self):
        self.assertEqual(sniff_mime_type(b"\xff\xd8\xff\xe0 rest"), "image/jpeg")

    def test_png_signature(self):
        self.assertEqual(sniff_mime_type(b"\x89PNG\r\n\x1a\n rest"), "image/png")

    def test_unrecognised_signature_returns_none(self):
        self.assertIsNone(sniff_mime_type(b"this is plain text, not any of the three types"))

    def test_wav_signature(self):
        # RIFF....WAVE -- the middle 4 bytes are a variable file-size field,
        # so this exercises the offset-based matcher, not a plain prefix.
        self.assertEqual(sniff_mime_type(b"RIFF\x24\x08\x00\x00WAVEfmt "), "audio/wav")

    def test_wav_rejects_other_riff_containers(self):
        # RIFF is also used by AVI/WEBP -- must not match on RIFF alone.
        self.assertIsNone(sniff_mime_type(b"RIFF\x24\x08\x00\x00WEBPfmt "))

    def test_mp3_id3_tagged_signature(self):
        self.assertEqual(sniff_mime_type(b"ID3\x03\x00\x00\x00\x00\x00\x00rest"), "audio/mpeg")

    def test_mp3_raw_frame_sync_signature(self):
        self.assertEqual(sniff_mime_type(b"\xff\xfb\x90\x00rest of frame"), "audio/mpeg")

    def test_m4a_signature(self):
        # ISO base media (MP4) container -- a 4-byte box size, then b"ftyp".
        self.assertEqual(sniff_mime_type(b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00"), "audio/mp4")

    def test_ogg_signature(self):
        self.assertEqual(sniff_mime_type(b"OggS\x00\x02rest"), "audio/ogg")

    def test_webm_signature(self):
        self.assertEqual(sniff_mime_type(b"\x1a\x45\xdf\xa3\x9f\x42rest"), "audio/webm")


class TestIsAudioMimeType(unittest.TestCase):
    def test_audio_types_recognised(self):
        for mime in ("audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/webm"):
            self.assertTrue(is_audio_mime_type(mime))

    def test_non_audio_types_rejected(self):
        for mime in ("application/pdf", "image/png", "image/jpeg"):
            self.assertFalse(is_audio_mime_type(mime))


class TestValidateUpload(unittest.TestCase):
    def test_valid_pdf_accepted(self):
        result = validate_upload("invoice.pdf", b"%PDF-1.4 content", "application/pdf", _MAX)
        self.assertTrue(result.ok)
        self.assertEqual(result.mime_type, "application/pdf")

    def test_valid_png_accepted(self):
        result = validate_upload("scan.png", b"\x89PNG\r\n\x1a\nrest", "image/png", _MAX)
        self.assertTrue(result.ok)

    def test_renamed_file_is_rejected_by_actual_content_not_extension(self):
        # A client renaming a plain-text file to ".pdf" must not fool
        # validation -- the real signature (absent here) is the source of
        # truth, not the extension.
        result = validate_upload("fake.pdf", b"just some plain text content", "application/pdf", _MAX)
        self.assertFalse(result.ok)
        self.assertIn("Unsupported file type", result.error)

    def test_extension_mismatching_real_content_is_rejected(self):
        # Real PDF bytes, but named .png -- extension disagrees with the
        # sniffed signature.
        result = validate_upload("mislabeled.png", b"%PDF-1.4 actually a pdf", "application/pdf", _MAX)
        self.assertFalse(result.ok)
        self.assertIn("does not match", result.error)

    def test_oversized_file_rejected(self):
        result = validate_upload("big.pdf", b"%PDF" + b"0" * (_MAX + 1), "application/pdf", _MAX)
        self.assertFalse(result.ok)
        self.assertIn("size limit", result.error)

    def test_empty_file_rejected(self):
        result = validate_upload("empty.pdf", b"", "application/pdf", _MAX)
        self.assertFalse(result.ok)

    def test_unrelated_declared_content_type_rejected(self):
        result = validate_upload("invoice.pdf", b"%PDF-1.4 content", "text/html", _MAX)
        self.assertFalse(result.ok)

    def test_octet_stream_declared_type_is_tolerated(self):
        # Browsers/clients commonly send application/octet-stream for
        # generic binary uploads -- this shouldn't be treated as a mismatch
        # on its own, since the actual signature is what's authoritative.
        result = validate_upload("invoice.pdf", b"%PDF-1.4 content", "application/octet-stream", _MAX)
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
