"""Unit tests for app.documents.image_forensics -- ELA + EXIF heuristics.

Regression coverage for a real bug found during manual verification: an
early version gated ELA on absolute mean error before computing
concentration, which silently discarded the exact signal the function
exists to find (confirmed via a real spliced-region test where peak tile
error was only 3/255). test_ela_low_absolute_error_does_not_zero_the_score
below exists specifically so that mistake can't come back unnoticed.

Run with (from backend/):
    python -m unittest tests.test_image_forensics -v
"""
from __future__ import annotations

import io
import math
import os
import random
import sys
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from app.documents import image_forensics  # noqa: E402


def _textured_photo(seed: int, w: int = 200, h: int = 200) -> Image.Image:
    """A procedural image with real per-pixel texture/noise -- smooth
    gradients compress to near-nothing under JPEG and are a poor stand-in
    for a photo; this isn't either, but it at least produces measurable,
    non-trivial per-tile compression error the way a real photo does."""
    rng = random.Random(seed)
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            r = int(128 + 60 * math.sin(x / 15.0) + 40 * math.sin(y / 23.0) + rng.randint(-25, 25))
            g = int(128 + 50 * math.sin((x + y) / 18.0) + rng.randint(-25, 25))
            b = int(128 + 45 * math.cos(x / 12.0 - y / 17.0) + rng.randint(-25, 25))
            px[x, y] = (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
    return img


def _jpeg_bytes(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _spliced_photo_bytes(seed: int) -> bytes:
    """Paste a small region re-compressed at a much lower quality into an
    otherwise-clean image, then save the whole thing at quality 90 -- the
    textbook case ELA is supposed to catch."""
    img = _textured_photo(seed)
    patch = img.crop((50, 50, 110, 110))
    patch = Image.open(io.BytesIO(_jpeg_bytes(patch, quality=15))).convert("RGB")
    img.paste(patch, (50, 50))
    return _jpeg_bytes(img, quality=90)


class TestComputeElaScore(unittest.TestCase):
    def test_non_image_bytes_returns_none(self):
        self.assertIsNone(image_forensics.compute_ela_score(b"not an image"))

    def test_clean_and_spliced_images_are_both_analyzable(self):
        clean = image_forensics.compute_ela_score(_jpeg_bytes(_textured_photo(1)))
        spliced = image_forensics.compute_ela_score(_spliced_photo_bytes(1))
        self.assertIsNotNone(clean)
        self.assertIsNotNone(spliced)
        self.assertIsInstance(clean["score"], float)
        self.assertGreaterEqual(clean["score"], 0.0)
        self.assertLessEqual(clean["score"], 1.0)

    def test_spliced_scores_higher_than_clean_on_average(self):
        # Not a guarantee for any single pair (this is a genuinely noisy
        # heuristic, see the module's own docstring) -- but across several
        # seeds, splicing should push the score up more often than not.
        clean_scores = [image_forensics.compute_ela_score(_jpeg_bytes(_textured_photo(s)))["score"] for s in range(5)]
        spliced_scores = [image_forensics.compute_ela_score(_spliced_photo_bytes(s))["score"] for s in range(5)]
        self.assertGreater(sum(spliced_scores) / 5, sum(clean_scores) / 5)

    def test_ela_low_absolute_error_does_not_zero_the_score(self):
        """Regression test: a real spliced-region case produced a tiny
        absolute mean error (peak tile 3/255) but a genuinely elevated,
        real concentration signal. An earlier version of this function
        gated on absolute magnitude and silently returned score=0.0 for
        exactly this case -- this test fails again if that regresses."""
        result = image_forensics.compute_ela_score(_spliced_photo_bytes(2))
        self.assertIsNotNone(result)
        self.assertGreater(result["score"], 0.0)


class TestCheckExifMetadata(unittest.TestCase):
    def _jpeg_with_exif(self, exif_dict) -> bytes:
        img = _textured_photo(0, w=50, h=50)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90, exif=exif_dict)
        return buf.getvalue()

    def test_non_image_bytes_returns_zero_score_no_crash(self):
        result = image_forensics.check_exif_metadata(b"not an image")
        self.assertEqual(result["score"], 0.0)
        self.assertFalse(result["had_exif"])

    def test_missing_exif_is_a_weak_not_zero_signal(self):
        raw = _jpeg_bytes(_textured_photo(0, w=50, h=50))
        result = image_forensics.check_exif_metadata(raw)
        self.assertFalse(result["had_exif"])
        self.assertGreater(result["score"], 0.0)
        self.assertLess(result["score"], 0.3)  # weak, not damning
        self.assertIn("routine", result["reasons"][0].lower())

    def test_editor_software_tag_flagged(self):
        img = _textured_photo(0, w=50, h=50)
        exif = img.getexif()
        exif[305] = "Adobe Photoshop 25.0"  # Software tag
        raw = self._jpeg_with_exif(exif)
        result = image_forensics.check_exif_metadata(raw)
        self.assertTrue(result["had_exif"])
        self.assertGreaterEqual(result["score"], 0.5)
        self.assertTrue(any("photoshop" in r.lower() for r in result["reasons"]))

    def test_camera_software_tag_not_flagged(self):
        img = _textured_photo(0, w=50, h=50)
        exif = img.getexif()
        exif[305] = "samsung SM-G991B"  # a real phone camera software string
        raw = self._jpeg_with_exif(exif)
        result = image_forensics.check_exif_metadata(raw)
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["reasons"], [])

    def test_inverted_timestamps_flagged(self):
        img = _textured_photo(0, w=50, h=50)
        exif = img.getexif()
        exif[36867] = "2026:01:10 10:00:00"  # DateTimeOriginal
        exif[306] = "2026:01:05 09:00:00"  # DateTime (modify) -- BEFORE original
        raw = self._jpeg_with_exif(exif)
        result = image_forensics.check_exif_metadata(raw)
        self.assertGreaterEqual(result["score"], 0.5)
        self.assertTrue(any("predates" in r.lower() for r in result["reasons"]))

    def test_consistent_timestamps_not_flagged(self):
        img = _textured_photo(0, w=50, h=50)
        exif = img.getexif()
        exif[36867] = "2026:01:05 09:00:00"  # DateTimeOriginal
        exif[306] = "2026:01:10 10:00:00"  # DateTime -- AFTER original, normal
        raw = self._jpeg_with_exif(exif)
        result = image_forensics.check_exif_metadata(raw)
        self.assertEqual(result["score"], 0.0)


class TestScoreDocumentImage(unittest.TestCase):
    def test_non_image_bytes_not_analyzable(self):
        result = image_forensics.score_document_image(b"not an image")
        self.assertFalse(result["analyzable"])
        self.assertEqual(result["combined_score"], 0.0)

    def test_combined_score_is_weighted_average_of_the_two_components(self):
        raw = _jpeg_bytes(_textured_photo(3))
        result = image_forensics.score_document_image(raw)
        expected = round(0.6 * result["ela_score"] + 0.4 * result["exif_score"], 3)
        self.assertEqual(result["combined_score"], expected)

    def test_reasons_from_both_checks_are_combined(self):
        img = _textured_photo(0, w=50, h=50)
        exif = img.getexif()
        exif[305] = "GIMP 2.10"
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90, exif=exif)
        result = image_forensics.score_document_image(buf.getvalue())
        self.assertTrue(any("gimp" in r.lower() for r in result["reasons"]))


if __name__ == "__main__":
    unittest.main()
