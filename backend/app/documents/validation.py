"""Upload validation: size cap + real MIME-type check via magic bytes.

Deliberately a manual signature check rather than the `python-magic` package
-- that needs the libmagic system library, which is exactly the kind of
Windows-dev-unfriendly system dependency the OCR module (see extraction.py)
already avoids for the same reason.

Checking the extension is not enough on its own: a client can rename any
file to ".pdf". The actual file signature is the source of truth; the
extension/declared content-type are only used to give a clearer error
message when they disagree with what the bytes actually are.

Audio signatures need more than a plain prefix match (WAV's "WAVE" marker
sits after a variable-length RIFF size field; M4A/MP4's "ftyp" box sits
after a size field too), so each entry carries a predicate over the raw
bytes rather than just a literal prefix.
"""

from __future__ import annotations

from typing import Callable

from .models import ValidationResult


def _prefix(sig: bytes) -> Callable[[bytes], bool]:
    return lambda raw: raw.startswith(sig)


def _is_wav(raw: bytes) -> bool:
    return raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"


def _is_m4a(raw: bytes) -> bool:
    # ISO base media (MP4) container: a 4-byte box size, then b"ftyp".
    # Specific brand after ftyp (M4A , isom, mp42, ...) varies by encoder.
    return raw[4:8] == b"ftyp"


# (sniffed mime_type, matcher, allowed extensions). Order matters where
# matchers could both apply to overlapping bytes -- none currently do.
_SIGNATURES: tuple[tuple[str, Callable[[bytes], bool], tuple[str, ...]], ...] = (
    ("application/pdf", _prefix(b"%PDF"), (".pdf",)),
    ("image/jpeg", _prefix(b"\xff\xd8\xff"), (".jpg", ".jpeg")),
    ("image/png", _prefix(b"\x89PNG\r\n\x1a\n"), (".png",)),
    # Audio evidence (voice notes, recorded calls) -- transcribed via Sarvam
    # Speech-to-Text, see extraction.py's _sarvam_transcribe().
    ("audio/wav", _is_wav, (".wav",)),
    ("audio/mpeg", _prefix(b"ID3"), (".mp3",)),
    # Raw MP3 frame sync (no ID3 tag) -- 0xFFFB/F3/F2/FA covers MPEG-1/2
    # Layer 3 with/without CRC, the encoders actually seen in the wild.
    ("audio/mpeg", _prefix(b"\xff\xfb"), (".mp3",)),
    ("audio/mpeg", _prefix(b"\xff\xf3"), (".mp3",)),
    ("audio/mpeg", _prefix(b"\xff\xf2"), (".mp3",)),
    ("audio/mp4", _is_m4a, (".m4a", ".mp4")),
    ("audio/ogg", _prefix(b"OggS"), (".ogg", ".opus")),
    ("audio/webm", _prefix(b"\x1a\x45\xdf\xa3"), (".webm",)),
)


def sniff_mime_type(raw: bytes) -> str | None:
    """Return the MIME type implied by the file's actual leading bytes, or
    None if it matches none of the types this feature accepts."""
    for mime_type, matches, _exts in _SIGNATURES:
        if matches(raw):
            return mime_type
    return None


def is_audio_mime_type(mime_type: str) -> bool:
    return mime_type.startswith("audio/")


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
            error=(
                "Unsupported file type -- only PDF, JPEG, PNG, and audio "
                "(WAV, MP3, M4A, OGG/OPUS, WEBM) are accepted."
            ),
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
