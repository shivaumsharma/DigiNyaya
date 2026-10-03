"""Image-level authenticity heuristics -- deliberately separate from
app.agents.preliminary_review.document_relevance()'s authenticity_flag, which
only ever reads OCR'd/extracted TEXT and explicitly cannot see the original
image bytes (its own prompt says so). This module does the opposite: it never
reads text, only the raw image bytes, using two deterministic, no-training-
data heuristics -- no model, nothing to keep in sync with a dataset.

HONESTY NOTE, read before wiring a score into anything user-facing: both
checks below are heuristic SIGNALS, not proof of tampering. Concretely:
  - Error Level Analysis flags LOCALIZED recompression-error concentration,
    but a legitimate crop/resize/re-save (very common -- WhatsApp and most
    sharing apps recompress on send) produces the same kind of signal.
  - EXIF forensics flags an editor's Software tag or an inverted timestamp,
    but most privacy-conscious upload flows strip EXIF entirely as routine
    behavior -- that produces a "clean" (no-signal) result that means
    "no metadata available to check", not "verified authentic". Missing
    EXIF is scored as a very weak signal for exactly this reason, not
    treated as suspicious on its own.
Neither check can distinguish "edited to defraud" from "cropped in a photo
app before uploading". Both belong in front of a human reviewer as a reason
to look closer, never as an automated finding of forgery.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from PIL import ExifTags, Image, ImageChops, UnidentifiedImageError

# Software tag substrings (case-insensitive) that name a general-purpose
# image editor rather than a camera/scanning app or "no software recorded".
# Deliberately just a recognisable-name list, not exhaustive -- absence of a
# match here is NOT evidence of anything, it just means this specific signal
# didn't fire.
_EDITOR_SOFTWARE_MARKERS = (
    "photoshop", "gimp", "snapseed", "lightroom", "picsart", "canva",
    "pixlr", "facetune", "affinity photo", "paint.net", "krita",
)

_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"

# ELA tuning constants -- provisional defaults, NOT calibrated against a
# real labeled photo dataset (this repo doesn't have one). Verified only
# that the underlying signal is directionally real: a synthetic clean-vs-
# spliced A/B test (5 seeds each, textured procedural images, a 120x120
# patch re-compressed at quality 15 pasted into an otherwise quality-90
# image) scored spliced images higher than clean ones in 5/5 seeds --
# clean averaged ~0.27, spliced ~0.37 -- but with real overlap between the
# two, which matches ELA's known real-world reputation as a genuinely noisy
# signal, not a precise detector. _IMAGE_AUTHENTICITY_FLAG_THRESHOLD in
# discrepancy.py was set low enough to fire on this synthetic signal at
# all; treat every constant here as a starting point that needs revisiting
# against real sample photos (genuine and edited) before being trusted for
# anything more than "worth a second look".
_ELA_JPEG_QUALITY = 90
_ELA_GRID = 16  # tiles per axis; error concentration is measured per-tile
_ELA_MIN_MEAN_ERROR = 0.05  # divide-by-zero guard only, NOT a signal-strength
# gate -- coefficient of variation (the actual concentration measure below)
# is scale-invariant, so a real, useful signal can exist even when the whole
# image's absolute recompression error is tiny (confirmed against a real
# spliced-region test: peak tile error 3/255, mean 0.86, and the pasted
# region was still clearly distinguishable from the background tiles).
# This floor only catches the genuine zero/near-zero case where mean_error
# is too close to 0 for the ratio itself to be numerically meaningful.
_ELA_CV_CAP = 1.5  # coefficient-of-variation value treated as "maximally
# concentrated" for normalization -- an image scoring at or above this is
# clamped to ela_score=1.0, not literally "more suspicious beyond this point"

_COMBINED_ELA_WEIGHT = 0.6
_COMBINED_EXIF_WEIGHT = 0.4


def compute_ela_score(raw: bytes) -> dict[str, Any] | None:
    """Error Level Analysis: re-save the image at a fixed JPEG quality, diff
    it pixel-wise against the original, and score how CONCENTRATED the error
    is in a small region versus spread uniformly across the image.

    Concentration, not raw magnitude, is the signal: uniform recompression
    (opening and re-saving the whole photo, which routine sharing/upload
    flows do constantly) raises error everywhere roughly equally. A region
    that was pasted in or edited at a different quality/generation than the
    rest of the image shows up as a tile (or cluster of tiles) with error
    well above the image's own baseline -- high coefficient of variation
    across tiles, not high error on its own.

    Returns None if the bytes can't be opened as an image at all (caller
    should treat that as "not analyzable", not as a clean result).
    """
    try:
        original = Image.open(io.BytesIO(raw))
        original.load()
        original = original.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return None

    resave_buffer = io.BytesIO()
    original.save(resave_buffer, format="JPEG", quality=_ELA_JPEG_QUALITY)
    resave_buffer.seek(0)
    resaved = Image.open(resave_buffer).convert("RGB")

    diff = ImageChops.difference(original, resaved).convert("L")

    # Average-pool the diff into a grid x grid thumbnail -- BOX resampling IS
    # block averaging, so each output pixel is exactly that tile's mean
    # error. Avoids a manual per-pixel loop (slow) and a numpy dependency
    # this backend doesn't otherwise need.
    grid_w = min(_ELA_GRID, diff.width) or 1
    grid_h = min(_ELA_GRID, diff.height) or 1
    tile_thumb = diff.resize((grid_w, grid_h), Image.Resampling.BOX)
    tile_values = list(tile_thumb.get_flattened_data())

    n = len(tile_values)
    if n == 0:
        return None
    mean_error = sum(tile_values) / n
    if mean_error < _ELA_MIN_MEAN_ERROR:
        # Genuinely nothing to measure -- the whole image re-compressed to
        # (near-)identical bytes, e.g. re-uploading the exact same JPEG file.
        # Deliberately a SMALL epsilon-style floor, not a "must have visible
        # compression loss" gate: concentration (coefficient of variation,
        # below) is scale-invariant and can be real even when the absolute
        # error is tiny -- gating on raw magnitude here would silently
        # discard the exact signal this function exists to find. Confirmed
        # against a real spliced-region test case: peak tile error was only
        # 3/255 (mean 0.86), yet the region the patch was pasted into was
        # still clearly, consistently higher than the surrounding tiles.
        return {
            "score": 0.0,
            "reasons": [],
            "note": "No measurable recompression difference at all -- nothing to assess.",
        }

    variance = sum((v - mean_error) ** 2 for v in tile_values) / n
    stdev = variance ** 0.5
    coefficient_of_variation = stdev / mean_error

    score = round(min(coefficient_of_variation / _ELA_CV_CAP, 1.0), 3)
    # Report the finding whenever there's a non-trivial score at all -- the
    # decision of what's actually flag-worthy belongs to the caller
    # (discrepancy.py's _IMAGE_AUTHENTICITY_FLAG_THRESHOLD on the COMBINED
    # score), not duplicated as a second, inconsistent cutoff here.
    reasons = []
    if score >= 0.15:
        max_tile = max(tile_values)
        reasons.append(
            f"Error-level analysis found a region with recompression error somewhat above "
            f"the image's own baseline (peak tile {max_tile:.0f} vs. mean {mean_error:.1f} "
            f"across a {grid_w}x{grid_h} grid) -- concentration score {score:.2f}."
        )
    return {"score": score, "reasons": reasons, "note": None}


def check_exif_metadata(raw: bytes) -> dict[str, Any]:
    """EXIF/metadata forensics: flag an editor-named Software tag, an
    inverted modify/original timestamp pair, or the complete absence of
    EXIF data. The last of these is intentionally the WEAKEST signal here --
    stripping EXIF on upload/share is routine, privacy-motivated, default
    behavior for a huge fraction of real photo-sharing paths, not something
    only an editor would do.
    """
    try:
        image = Image.open(io.BytesIO(raw))
        exif = image.getexif()
    except (UnidentifiedImageError, OSError, ValueError):
        return {"score": 0.0, "reasons": [], "had_exif": False}

    if not exif or len(exif) == 0:
        return {
            "score": 0.15,
            "reasons": [
                "No EXIF metadata found. This is commonly routine (many apps strip EXIF on "
                "upload/share) rather than a sign of editing -- treated as a weak signal only."
            ],
            "had_exif": False,
        }

    tags = {ExifTags.TAGS.get(tag_id, tag_id): value for tag_id, value in exif.items()}
    reasons: list[str] = []
    score = 0.0

    software = str(tags.get("Software") or "").strip()
    if software and any(marker in software.lower() for marker in _EDITOR_SOFTWARE_MARKERS):
        score += 0.5
        reasons.append(f"Software tag names an image editor: \"{software}\".")

    original_dt_raw = tags.get("DateTimeOriginal")
    modify_dt_raw = tags.get("DateTime")
    if original_dt_raw and modify_dt_raw:
        try:
            original_dt = datetime.strptime(str(original_dt_raw), _EXIF_DATETIME_FORMAT)
            modify_dt = datetime.strptime(str(modify_dt_raw), _EXIF_DATETIME_FORMAT)
            if modify_dt < original_dt:
                score += 0.5
                reasons.append(
                    f"Modify timestamp ({modify_dt_raw}) predates the original capture "
                    f"timestamp ({original_dt_raw})."
                )
        except ValueError:
            pass  # unparseable timestamp -- not evidence of anything, just skip this check

    return {"score": round(min(score, 1.0), 3), "reasons": reasons, "had_exif": True}


def score_document_image(raw: bytes) -> dict[str, Any]:
    """Combine ELA + EXIF into one score, same weighted-formula style as
    discrepancy.py's 0.6/0.4 confidence formula. ELA is weighted higher
    (0.6) because it looks at the actual pixel data, however noisily; EXIF
    (0.4) is weighted lower because it is trivially stripped or spoofed and
    routinely absent for reasons that have nothing to do with editing.

    Returns analyzable=False when the bytes couldn't be opened as an image
    at all -- callers must treat that as "couldn't check", not as a clean
    0.0 result.
    """
    ela = compute_ela_score(raw)
    exif = check_exif_metadata(raw)

    ela_score = ela["score"] if ela is not None else 0.0
    exif_score = exif["score"]
    combined = round(_COMBINED_ELA_WEIGHT * ela_score + _COMBINED_EXIF_WEIGHT * exif_score, 3)

    reasons = list(ela["reasons"]) if ela is not None else []
    reasons.extend(exif["reasons"])

    return {
        "combined_score": combined,
        "ela_score": ela_score,
        "exif_score": exif_score,
        "analyzable": ela is not None,
        "reasons": reasons,
    }
