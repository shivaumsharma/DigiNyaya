"""Internal dataclasses for the documents package -- NOT API response schemas
(those live in app.models, per the rest of the codebase's convention)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    mime_type: str | None = None
    error: str | None = None


@dataclass
class ExtractionResult:
    raw_text: str
    cleaned_text: str
    pages: list[str] = field(default_factory=list)
    is_scanned: bool = False
    # None for native-text documents (no OCR uncertainty); 0..1 for OCR'd ones.
    ocr_confidence: float | None = None
    engine: str = "native"  # "native" | "tesseract"
    error: str | None = None
