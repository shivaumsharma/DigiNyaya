"""Upload validation: size cap + real MIME-type check via magic bytes.

Deliberately a manual signature check rather than the `python-magic` package
-- that needs the libmagic system library, which is exactly the kind of
Windows-dev-unfriendly system dependency the OCR module (see extraction.py)
already avoids for the same reason. Three signatures covers everything this
feature accepts (PDF, JPEG, PNG), so a full magic-number database is
overkill.

Checking the extension is not enough on its own: a client can rename any
file to ".pdf". The actual file signature is the source of truth; the
extension/declared content-type are only used to give a clearer error
message when they disagree with what the bytes actually are.
"""

from __future__ import annotations

from .models import ValidationResult

# (sniffed mime_type, signature bytes, allowed extensions)
_SIGNATURES: tuple[tuple[str, bytes, tuple[str, ...]], ...] = (
    ("application/pdf", b"%PDF", (".pdf",)),
    ("image/jpeg", b"\xff\xd8\xff", (".jpg", ".jpeg")),
    ("image/png", b"\x89PNG\r\n\x1a\n", (".png",)),
)


def sniff_mime_type(raw: bytes) -> str | None:
    """Return the MIME type implied by the file's actual leading bytes, or
    None if it matches none of the types this feature accepts."""
    for mime_type, signature, _exts in _SIGNATURES:
        if raw.startswith(signature):
            return mime_type
    return None


def _extension(filename: str) -> str:
    idx = filename.rfind(".")
    return filename[idx:].lower() if idx != -1 else ""


def validate_upload(
    filename: str,
    raw: bytes,
    declared_content_type: str | None,
    max_size_bytes: int,
) -> ValidationResult:
    if len(raw) > max_size_bytes:
        return ValidationResult(
            ok=False,
            error=f"File exceeds the {max_size_bytes // (1024 * 1024)}MB size limit.",
        )
    if not raw:
        return ValidationResult(ok=False, error="Empty file.")

    mime_type = sniff_mime_type(raw)
    if mime_type is None:
        return ValidationResult(
            ok=False,
            error="Unsupported file type -- only PDF, JPEG, and PNG are accepted.",
        )

    ext = _extension(filename)
    allowed_exts = next(exts for m, _s, exts in _SIGNATURES if m == mime_type)
    if ext and ext not in allowed_exts:
        return ValidationResult(
            ok=False,
            error=(
                f"File extension '{ext}' does not match its actual content "
                f"(detected {mime_type}). Rename or re-export the file."
            ),
        )
    # declared_content_type is client-supplied and untrusted -- only used
    # for a friendlier error message, never as the basis for acceptance.
    if declared_content_type and declared_content_type.split(";")[0].strip() not in (
        mime_type,
        "application/octet-stream",
    ):
        return ValidationResult(
            ok=False,
            error=(
                f"Declared content-type '{declared_content_type}' does not match "
                f"actual file content (detected {mime_type})."
            ),
        )
    return ValidationResult(ok=True, mime_type=mime_type)
