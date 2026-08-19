"""Text extraction from uploaded documents: native PDF text layer, OCR
fallback for scanned PDFs/images, and audio transcription for voice-note /
recorded-call evidence.

Uses PyMuPDF (``fitz``) for BOTH the text-layer check/extraction AND
rasterizing scanned pages to images -- this avoids needing ``pdf2image``
(which requires the poppler system binary, an extra install this dev
environment doesn't have and Windows makes awkward).

OCR itself tries Sarvam's Document AI (``doc_ai.digitise``, Sarvam Vision
1.5) first, falling back to local Tesseract if no API key is configured, the
document exceeds Document AI's 10-page cap, or the API call fails for any
reason -- same graceful-degradation shape as the rest of this codebase's LLM
provider fallbacks. Sarvam matters for two concrete reasons: (1) it's a
cloud API call, not a local binary, so it works in any deploy environment
regardless of whether tesseract-ocr is installed on the host; (2) it
natively covers all 22 Indian languages, whereas TESSERACT_LANG below
defaults to "eng" only -- Indic-language evidence images were silently
OCR'ing to near-empty/garbage text before this fell back to Tesseract.

Audio evidence (WAV/MP3/M4A/OGG/WEBM -- see app.documents.validation) is
transcribed via Sarvam's Speech-to-Text Batch API with speaker diarization,
in ``_sarvam_transcribe``. There is no local fallback for audio: if Sarvam
isn't configured or the call fails, extraction reports a failure rather than
silently producing empty text.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import tempfile
import time
from typing import Literal

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

from ..core.circuit_breaker import CircuitBreaker
from ..llm.config import config
from .models import ExtractionResult
from .validation import is_audio_mime_type

logger = logging.getLogger("diginyaya.documents")

# Sarvam Document AI: async job API (submit -> poll status -> fetch
# results). Safe to block-poll here since extract_document() always runs
# inside a background job thread (see app/jobs.py's run_extraction), never a
# request handler.
_SARVAM_DOC_AI_MAX_PAGES = 10  # hard API limit; PDFs over this fall back to Tesseract.
_SARVAM_DOC_AI_POLL_INTERVAL = 3.0
_SARVAM_DOC_AI_POLL_TIMEOUT = 120.0
_SARVAM_DOC_AI_TERMINAL = {"completed", "partially_completed", "failed", "rejected"}
# Own breaker per Sarvam sub-API (OCR vs STT fail independently). Without
# this, a degraded/hanging Document AI job pays the full poll timeout above
# (up to 120s) on every single call, tying up a background job thread each
# time -- Tesseract is still the local fallback (see below) so a Sarvam
# outage degrades rather than fails outright, but there's no reason for
# every one of those calls to pay the full 120s first.
_ocr_breaker = CircuitBreaker(name="sarvam_doc_ai")


def _sarvam_ocr(raw: bytes, filename: str, mime_type: str, language: str) -> tuple[str, float] | None:
    """OCR via Sarvam Document AI. Returns (text, confidence) or None if the
    provider is unavailable/unconfigured/failed -- callers fall back to
    Tesseract on None rather than raising, matching this codebase's existing
    provider-fallback convention (e.g. app.rag.index's keyword fallback)."""
    if not config.sarvam_api_key:
        return None
    if not _ocr_breaker.allow():
        logger.info("sarvam doc ai circuit breaker open; skipping call", extra={"event": "sarvam_doc_ai_breaker_open"})
        return None
    try:
        from sarvamai import SarvamAI
    except ImportError:
        return None

    try:
        client = SarvamAI(api_subscription_key=config.sarvam_api_key)
        job = client.doc_ai.digitise(
            file=[(filename, raw, mime_type)],
            language=language,
            output_format="md",
        )
        status = job.status
        deadline = time.monotonic() + _SARVAM_DOC_AI_POLL_TIMEOUT
        while status not in _SARVAM_DOC_AI_TERMINAL:
            if time.monotonic() > deadline:
                logger.warning("sarvam doc ai job timed out", extra={"event": "sarvam_doc_ai_timeout", "job_id": job.job_id})
                _ocr_breaker.record_failure()  # no response at all -- an availability signal, not a content one
                return None
            time.sleep(_SARVAM_DOC_AI_POLL_INTERVAL)
            status = client.doc_ai.get_status(job.job_id).status

        if status == "failed" or status == "rejected":
            logger.warning("sarvam doc ai job failed", extra={"event": "sarvam_doc_ai_job_failed", "job_id": job.job_id, "status": status})
            _ocr_breaker.record_success()  # Sarvam responded fine; this document specifically failed, not the service
            return None

        # format="json" returns page.blocks (list of {"text", "reading_order", ...}
        # dicts), not page.content -- .content is only populated for the
        # html/md primary-file download, which we don't fetch here.
        results = client.doc_ai.get_results(job.job_id, format="json")
        pages_text = []
        for doc in (results.documents or []):
            for page in (doc.pages or []):
                blocks = getattr(page, "blocks", None) or []
                ordered = sorted(blocks, key=lambda b: b.get("reading_order") or 0)
                page_text = "\n".join(b["text"] for b in ordered if b.get("text"))
                if page_text:
                    pages_text.append(page_text)
        if not pages_text:
            _ocr_breaker.record_success()  # service responded; this document just had no extractable text
            return None
        text = "\n\n".join(pages_text)
        # partially_completed means some pages failed -- keep the text we got,
        # but flag lower confidence so callers/UI can be honest about it.
        confidence = 0.92 if status == "completed" else 0.6
        _ocr_breaker.record_success()
        return text, confidence
    except Exception as exc:
        logger.warning("sarvam doc ai call failed", extra={"event": "sarvam_doc_ai_error", "error": str(exc)})
        _ocr_breaker.record_failure()
        return None


# Sarvam Speech-to-Text Batch API: same submit -> upload -> poll -> download
# job shape as Document AI, but the SDK's job helpers want local file paths
# (upload_files(file_paths=...), download_outputs(output_dir=...)) rather
# than in-memory bytes, so this writes to a scratch temp dir and cleans it
# up in a finally block -- the only place in this module that touches disk.
_SARVAM_STT_POLL_INTERVAL = 5
_SARVAM_STT_TIMEOUT = 600  # 10 minutes; Sarvam's own SDK default, generous for a call recording or voice note.
_SARVAM_STT_EXT_BY_MIME = {
    "audio/wav": ".wav", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
    "audio/ogg": ".ogg", "audio/webm": ".webm",
}
# Own breaker, independent of _ocr_breaker -- see that breaker's comment;
# STT (speech_to_text_job) and Document AI (doc_ai) are different Sarvam
# sub-APIs. Unlike OCR, there is no local fallback for audio, so a hanging
# STT call (up to _SARVAM_STT_TIMEOUT = 600s) is the worst-case latency in
# this whole module without this breaker.
_stt_breaker = CircuitBreaker(name="sarvam_stt")


def _sarvam_transcribe(raw: bytes, mime_type: str) -> tuple[str, float] | None:
    """Transcribe audio evidence via Sarvam's Speech-to-Text Batch API, with
    speaker diarization so a recorded call/voice note comes back as "Speaker
    0: ... / Speaker 1: ..." rather than one undifferentiated blob. Returns
    (text, confidence) or None if unavailable/unconfigured/failed.

    Unlike OCR, there is no local fallback engine for audio -- a None here
    surfaces as extraction_status: "failed", the same graceful-failure
    contract used everywhere else in this module.

    language_code is deliberately "unknown" (auto-detect) rather than the
    case's source_language: that field describes the language the claimant
    *typed* their complaint in, which is a different, not-reliably-related
    question from what language a recorded call or voice note happens to be
    in -- Sarvam's own per-audio detection is the more trustworthy signal.
    """
    if not config.sarvam_api_key:
        return None
    if not _stt_breaker.allow():
        logger.info("sarvam stt circuit breaker open; skipping call", extra={"event": "sarvam_stt_breaker_open"})
        return None
    try:
        from sarvamai import SarvamAI
    except ImportError:
        return None

    tmp_dir = tempfile.mkdtemp(prefix="diginyaya_stt_")
    try:
        ext = _SARVAM_STT_EXT_BY_MIME.get(mime_type, ".wav")
        input_path = os.path.join(tmp_dir, f"evidence{ext}")
        with open(input_path, "wb") as fh:
            fh.write(raw)

        client = SarvamAI(api_subscription_key=config.sarvam_api_key)
        job = client.speech_to_text_job.create_job(
            model="saaras:v3",
            mode="transcribe",
            language_code="unknown",
            with_diarization=True,
        )
        job.upload_files(file_paths=[input_path])
        job.start()
        job.wait_until_complete(poll_interval=_SARVAM_STT_POLL_INTERVAL, timeout=_SARVAM_STT_TIMEOUT)

        file_results = job.get_file_results()
        if not file_results["successful"]:
            logger.warning(
                "sarvam stt job had no successful files",
                extra={"event": "sarvam_stt_job_failed", "job_id": job.job_id, "failed": file_results["failed"]},
            )
            _stt_breaker.record_success()  # service responded; this file specifically failed, not the service
            return None

        out_dir = os.path.join(tmp_dir, "output")
        job.download_outputs(output_dir=out_dir)
        json_files = [f for f in os.listdir(out_dir) if f.endswith(".json")]
        if not json_files:
            _stt_breaker.record_success()
            return None
        with open(os.path.join(out_dir, json_files[0]), encoding="utf-8") as fh:
            data = json.load(fh)

        entries = (data.get("diarized_transcript") or {}).get("entries")
        if entries:
            text = "\n".join(
                f"Speaker {e['speaker_id']}: {e['transcript']}"
                for e in entries if e.get("transcript")
            )
        else:
            text = data.get("transcript") or ""

        if not text:
            _stt_breaker.record_success()
            return None
        # Batch STT doesn't return a confidence score at all (unlike Document
        # AI's job status); fixed value, consistent with this module's other
        # "no real score available" cases.
        _stt_breaker.record_success()
        return text, 0.9
    except Exception as exc:
        logger.warning("sarvam stt call failed", extra={"event": "sarvam_stt_error", "error": str(exc)})
        _stt_breaker.record_failure()
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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


def extract_document(raw: bytes, mime_type: str, *, language: str = "en-IN") -> ExtractionResult:
    """Top-level orchestrator: detects native-vs-scanned for PDFs, OCRs
    scanned PDFs and standalone images, and normalizes the result. Never
    raises -- extraction failures are reported via ExtractionResult.error so
    callers (jobs.py) can mark the document extraction_status='failed'
    instead of crashing the background job thread.

    ``language`` is a BCP-47 code (e.g. "hi-IN") passed to Sarvam Document AI
    for OCR accuracy -- callers should pass the case's actual filing
    language when known; defaults to "en-IN"."""
    try:
        if is_audio_mime_type(mime_type):
            sarvam_result = _sarvam_transcribe(raw, mime_type)
            if sarvam_result is None:
                return ExtractionResult(
                    raw_text="", cleaned_text="",
                    error="Audio transcription is unavailable right now -- Sarvam Speech-to-Text is not configured or the call failed.",
                )
            text, confidence = sarvam_result
            cleaned = normalize_text(text)
            logger.info(
                "document extracted (STT, audio)",
                extra={"event": "document_extracted", "engine": "sarvam_stt", "ocr_confidence": confidence},
            )
            return ExtractionResult(
                raw_text=text, cleaned_text=cleaned, pages=[text],
                is_scanned=True, ocr_confidence=confidence, engine="sarvam_stt",
            )

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

            doc = fitz.open(stream=raw, filetype="pdf")
            page_count = doc.page_count
            doc.close()
            if page_count <= _SARVAM_DOC_AI_MAX_PAGES:
                sarvam_result = _sarvam_ocr(raw, "document.pdf", mime_type, language)
                if sarvam_result is not None:
                    text, confidence = sarvam_result
                    cleaned = normalize_text(text)
                    logger.info(
                        "document extracted (OCR)",
                        extra={"event": "document_extracted", "engine": "sarvam_doc_ai", "pages": page_count, "ocr_confidence": confidence},
                    )
                    return ExtractionResult(
                        raw_text=text, cleaned_text=cleaned, pages=[text],
                        is_scanned=True, ocr_confidence=confidence, engine="sarvam_doc_ai",
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
        sarvam_result = _sarvam_ocr(raw, "image", mime_type, language)
        if sarvam_result is not None:
            text, confidence = sarvam_result
            cleaned = normalize_text(text)
            logger.info(
                "document extracted (OCR, image)",
                extra={"event": "document_extracted", "engine": "sarvam_doc_ai", "ocr_confidence": confidence},
            )
            return ExtractionResult(
                raw_text=text, cleaned_text=cleaned, pages=[text],
                is_scanned=True, ocr_confidence=confidence, engine="sarvam_doc_ai",
            )

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
