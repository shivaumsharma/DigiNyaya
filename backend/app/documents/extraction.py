"""Text extraction from uploaded documents: native PDF text layer, OCR
fallback for scanned PDFs/images.

Uses PyMuPDF (``fitz``) for BOTH the text-layer check/extraction AND
rasterizing scanned pages to images -- this avoids needing ``pdf2image``
(which requires the poppler system binary, an extra install this dev
environment doesn't have and Windows makes awkward). Tesseract does the
actual OCR once a page is an image either way (a rasterized PDF page or a
directly-uploaded JPEG/PNG).

Sarvam evaluated for OCR: Sarvam's docs reference a "Document
Intelligence"/vision product, but it isn't implemented in
app.llm.providers.sarvam today and the detailed API docs weren't reachable
during evaluation (see the README section this feature adds). Tesseract is
the working default; swapping in a Sarvam-based path later only requires a
new implementation of ``ocr_image``'s contract, not a redesign.
"""

from __future__ import annotations

import io
import logging
import os
import re
from typing import Literal

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

from .models import ExtractionResult

logger = logging.getLogger("diginyaya.documents")

# Language packs to pass to Tesseract, e.g. "eng+hin+ben". Defaults to
# English only, since that's the one pack guaranteed present on any fresh
# Tesseract install -- Indic packs must be installed separately (see the
# README section this feature adds) and opted into via this env var, rather
# than assumed present (Tesseract raises if a requested pack isn't
# installed, which would break OCR entirely on a machine without them).
TESSERACT_LANG = os.getenv("TESSERACT_LANG", "eng")

# A native-text PDF page from a real document typically has hundreds of
# characters; a scanned page with no text layer reports 0. A small
# threshold (rather than requiring literally 0) tolerates a stray
# OCR'd-title-only layer some scanners embed without treating the page as
# "native".
_NATIVE_TEXT_CHARS_PER_PAGE_THRESHOLD = 20

# Collapses runs of whitespace and drops lines that are almost entirely
# non-alphanumeric (common OCR noise -- stray punctuation/box-drawing
# artifacts from scan borders) without attempting full spell-correction,
# which would risk silently rewriting evidence text.
_WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
_NOISE_LINE_RE = re.compile(r"^[\W_]{1,}$")


def detect_pdf_type(raw: bytes) -> Literal["native", "scanned"]:
    doc = fitz.open(stream=raw, filetype="pdf")
    try:
        total_chars = sum(len(page.get_text()) for page in doc)
        avg_chars_per_page = total_chars / max(doc.page_count, 1)
    finally:
        doc.close()
    return "native" if avg_chars_per_page >= _NATIVE_TEXT_CHARS_PER_PAGE_THRESHOLD else "scanned"


def extract_native_text(raw: bytes) -> list[str]:
    doc = fitz.open(stream=raw, filetype="pdf")
    try:
        return [page.get_text() for page in doc]
    finally:
        doc.close()


def rasterize_pages(raw: bytes, dpi: int = 200) -> list[Image.Image]:
    doc = fitz.open(stream=raw, filetype="pdf")
    try:
        images = []
        for page in doc:
            pixmap = page.get_pixmap(dpi=dpi)
            mode = "RGBA" if pixmap.alpha else "RGB"
            images.append(Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples))
        return images
    finally:
        doc.close()


def ocr_image(image: Image.Image) -> tuple[str, float]:
    """Return (extracted_text, confidence 0..1). Confidence is the mean
    per-word Tesseract confidence (0-100, -1 for non-word regions which are
    excluded), normalized to 0..1."""
    data = pytesseract.image_to_data(image, lang=TESSERACT_LANG, output_type=pytesseract.Output.DICT)
    words = [w for w in data.get("text", []) if w.strip()]
    confidences = [int(c) for c, w in zip(data.get("conf", []), data.get("text", [])) if w.strip() and int(c) >= 0]
    text = " ".join(words)
    confidence = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0
    return text, confidence


def normalize_text(raw_text: str) -> str:
    lines = []
    for line in raw_text.splitlines():
        line = _WHITESPACE_RE.sub(" ", line).strip()
        if not line or _NOISE_LINE_RE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def extract_document(raw: bytes, mime_type: str) -> ExtractionResult:
    """Top-level orchestrator: detects native-vs-scanned for PDFs, OCRs
    scanned PDFs and standalone images, and normalizes the result. Never
    raises -- extraction failures are reported via ExtractionResult.error so
    callers (jobs.py) can mark the document extraction_status='failed'
    instead of crashing the background job thread."""
    try:
        if mime_type == "application/pdf":
            pdf_type = detect_pdf_type(raw)
            if pdf_type == "native":
                pages = extract_native_text(raw)
                raw_text = "\n".join(pages)
                cleaned = normalize_text(raw_text)
                logger.info(
                    "document extracted (native)",
                    extra={"event": "document_extracted", "engine": "native", "pages": len(pages)},
                )
                return ExtractionResult(
                    raw_text=raw_text, cleaned_text=cleaned, pages=pages,
                    is_scanned=False, ocr_confidence=None, engine="native",
                )

            images = rasterize_pages(raw)
            page_texts: list[str] = []
            confidences: list[float] = []
            for image in images:
                text, conf = ocr_image(image)
                page_texts.append(text)
                confidences.append(conf)
            raw_text = "\n".join(page_texts)
            cleaned = normalize_text(raw_text)
            mean_conf = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
            logger.info(
                "document extracted (OCR)",
                extra={"event": "document_extracted", "engine": "tesseract", "pages": len(images), "ocr_confidence": mean_conf},
            )
            return ExtractionResult(
                raw_text=raw_text, cleaned_text=cleaned, pages=page_texts,
                is_scanned=True, ocr_confidence=mean_conf, engine="tesseract",
            )

        # JPEG/PNG -- always OCR'd directly, no PDF text-layer question.
        image = Image.open(io.BytesIO(raw))
        text, conf = ocr_image(image)
        cleaned = normalize_text(text)
        logger.info(
            "document extracted (OCR, image)",
            extra={"event": "document_extracted", "engine": "tesseract", "ocr_confidence": conf},
        )
        return ExtractionResult(
            raw_text=text, cleaned_text=cleaned, pages=[text],
            is_scanned=True, ocr_confidence=round(conf, 3), engine="tesseract",
        )
    except Exception as exc:
        logger.warning(
            "document extraction failed",
            extra={"event": "document_extraction_failed", "error": str(exc)},
        )
        return ExtractionResult(raw_text="", cleaned_text="", error=str(exc))
