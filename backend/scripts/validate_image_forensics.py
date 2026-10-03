"""Validate app.documents.image_forensics.score_document_image() against a
REAL, larger dataset of genuine vs. forged document images, replacing the
module's own docstring admission that it was only ever checked against 5
synthetic clean/spliced pairs (procedurally-generated textures, not real
documents).

Dataset: zodumair/sifta-document-forgery-dataset on HuggingFace (192 document
images, roughly balanced real/forged, free/anonymous download via the
`datasets` library -- no account or API key needed, same access pattern
already used by scripts/source_free_judgments.py for court judgments).

HONESTY NOTE: this dataset's own README does not document how its forgeries
were created (real edits vs. some other method), and carries no explicit
license statement. That is a real limitation to disclose alongside any
result from this script -- it is a genuine improvement in scale and realism
over 5 synthetic pairs, not a fully-provenanced forensic benchmark. A
receipt-specific, documented-methodology alternative (the ICDAR 2023 "Find
it again!" dataset, 988 receipts / 163 realistically forged) exists but is
gated behind an academic request form, not a free anonymous download.

Run (from backend/): python -m scripts.validate_image_forensics
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

import io  # noqa: E402
from datasets import load_dataset  # noqa: E402
from sklearn.metrics import roc_auc_score, accuracy_score  # noqa: E402

from app.documents.image_forensics import score_document_image  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parent.parent / "data_cache"
OUT_PATH = _DATA_DIR / "image_forensics_validation_report.json"


def main() -> int:
    print("Downloading zodumair/sifta-document-forgery-dataset (HuggingFace, free/anonymous)...")
    ds = load_dataset("zodumair/sifta-document-forgery-dataset")
    print({split: len(ds[split]) for split in ds})

    # Combine all splits -- we're validating the heuristic itself, not
    # training anything, so there's no train/test leakage concern here.
    all_rows = []
    for split in ds:
        all_rows.extend(ds[split])

    label_col = None
    for candidate in ("label", "labels", "class"):
        if candidate in all_rows[0]:
            label_col = candidate
            break
    if label_col is None:
        print("Could not find a label column. Columns present:", list(all_rows[0].keys()))
        return 1

    image_col = "image" if "image" in all_rows[0] else None
    if image_col is None:
        print("Could not find an image column. Columns present:", list(all_rows[0].keys()))
        return 1

    print(f"Using label column '{label_col}', image column '{image_col}'. "
          f"Label values seen: {set(r[label_col] for r in all_rows[:20])}")

    scores, labels, failures = [], [], 0
    for i, row in enumerate(all_rows):
        img = row[image_col]
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=95)
        raw = buf.getvalue()
        result = score_document_image(raw)
        if not result["analyzable"]:
            failures += 1
            continue
        scores.append(result["combined_score"])
        labels.append(int(row[label_col]))
        if (i + 1) % 50 == 0:
            print(f"  processed {i + 1}/{len(all_rows)}")

    print(f"\nScored {len(scores)} image(s), {failures} unanalyzable.")
    print(f"Label distribution: {sum(labels)} positive (forged, assuming label=1 is forged) / {len(labels) - sum(labels)} negative")

    auc = roc_auc_score(labels, scores)
    print(f"\nROC-AUC (combined_score vs. forged label, as-labeled): {auc:.3f}")
    print("(If this is far below 0.5, the label polarity is likely inverted -- check which class is '1' "
          "in the dataset card and re-run with labels flipped before trusting this number.)")

    # Best-threshold accuracy for reference, not a claim of a "right"
    # threshold -- the module itself is explicit that it feeds a human
    # reviewer's judgment, not an automated accept/reject gate.
    best_acc, best_t = 0.0, 0.0
    for t in [i / 100 for i in range(0, 101, 2)]:
        preds = [1 if s >= t else 0 for s in scores]
        acc = accuracy_score(labels, preds)
        if acc > best_acc:
            best_acc, best_t = acc, t
    print(f"Best-threshold accuracy: {best_acc:.3f} at threshold {best_t}")

    import json
    OUT_PATH.write_text(json.dumps({
        "dataset": "zodumair/sifta-document-forgery-dataset",
        "n_scored": len(scores),
        "n_unanalyzable": failures,
        "n_forged_label": sum(labels),
        "n_genuine_label": len(labels) - sum(labels),
        "roc_auc": round(auc, 3),
        "best_threshold_accuracy": round(best_acc, 3),
        "best_threshold": best_t,
        "honesty_note": (
            "Dataset's forgery-creation methodology and license are undocumented in its README. "
            "A real, larger, more realistic test than the module's original 5-vs-5 synthetic pairs, "
            "but not a fully-provenanced forensic benchmark. See module docstring."
        ),
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
