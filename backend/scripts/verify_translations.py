"""Back-translation sanity check for the static UI dictionaries.

One-off/dev-time QA tool, not a runtime API. For every string in
frontend/src/i18n/<lang>.json, translates it back to English via Sarvam
(same /translate endpoint translate_ui_strings.py used to generate the
files) and compares the round-trip to the real en.json string. A low
similarity score usually means the original forward translation drifted in
meaning, not just phrasing -- worth a human's eyes. This only catches gross
mistranslation, not tone/idiom/fluency; it is a first pass, not a
replacement for a native speaker's read-through.

Also flags two structural issues no similarity score would catch:
  - a {{placeholder}} token present in en.json but missing from the
    translated string (would render literally/crash `t()`'s interpolation)
  - a key present in en.json with no counterpart in the target file at all

Usage (from the backend folder):
    python scripts/verify_translations.py [lang_code ...]
    python scripts/verify_translations.py            # all 9 non-English languages
    python scripts/verify_translations.py hi ta       # just these
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, ".")

from app.language.translator import get_translator  # noqa: E402

I18N_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"
REPORT_DIR = Path(__file__).resolve().parent / "translation_qa"

# All languages the frontend selector offers except English itself (see
# app/language/config.py SUPPORTED_LANGUAGES).
ALL_LANGS = ["hi", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa", "od"]

PLACEHOLDER_RE = re.compile(r"\{\{\w+\}\}")
_WORD_RE = re.compile(r"[a-z0-9]+")

# A handful of high-frequency function words that add noise to overlap
# scoring without carrying meaning -- dropped before comparing word sets.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "of", "to", "in", "on", "or", "and",
    "with", "your", "you", "it", "this", "that", "for", "by", "at",
}


def word_set(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def word_overlap(a: str, b: str) -> float:
    """Jaccard similarity over content-word sets. Order- and
    phrasing-independent (unlike difflib.SequenceMatcher on raw characters,
    which penalizes legitimate rewording heavily) -- a back-translation that
    reorders clauses or swaps a synonym should not be flagged just for that.
    """
    set_a, set_b = word_set(a), word_set(b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


# Below this round-trip similarity, flag for human review. Calibrated
# against a live pilot run (see conversation), not a formal study --
# back-translations legitimately reword a lot, so this is tuned to catch
# drift in *meaning* (missing/added content words), not just phrasing.
SIMILARITY_FLOOR = 0.30


def flatten(node, prefix: str = "") -> dict[str, str]:
    """Flatten nested dicts/lists into {dotted.path[index]: leaf-string},
    skipping non-string and empty leaves (e.g. the empty "footerRight").
    """
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            out.update(flatten(value, path))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.update(flatten(value, f"{prefix}[{index}]"))
    elif isinstance(node, str):
        if node.strip():
            out[prefix] = node
    return out


@dataclass
class Finding:
    key: str
    kind: str  # "missing_key" | "placeholder_mismatch" | "low_similarity" | "too_short"
    english: str
    translated: str | None = None
    back_translated: str | None = None
    similarity: float | None = None
    missing_placeholders: list[str] | None = None


# Below this many content words, back-translation round-trips are too noisy
# to trust (pilot run: "Always" -> "हमेशा" -> "Hamisha" -- correct Hindi,
# but Sarvam transliterated instead of translating it back). These are
# listed separately for a human to eyeball directly rather than silently
# skipped or falsely flagged.
MIN_WORDS_FOR_AUTO_CHECK = 4


def check_language(lang: str, flat_en: dict[str, str], translator) -> list[Finding]:
    path = I18N_DIR / f"{lang}.json"
    if not path.exists():
        print(f"  SKIP {lang}: {path} not found")
        return []

    flat_target = flatten(json.loads(path.read_text(encoding="utf-8")))
    findings: list[Finding] = []

    for key, en_value in flat_en.items():
        target_value = flat_target.get(key)
        if target_value is None:
            findings.append(Finding(key=key, kind="missing_key", english=en_value))
            continue

        en_placeholders = set(PLACEHOLDER_RE.findall(en_value))
        target_placeholders = set(PLACEHOLDER_RE.findall(target_value))
        missing = sorted(en_placeholders - target_placeholders)
        if missing:
            findings.append(
                Finding(
                    key=key,
                    kind="placeholder_mismatch",
                    english=en_value,
                    translated=target_value,
                    missing_placeholders=missing,
                )
            )
            # A broken interpolation token is worth flagging on its own, but
            # back-translation similarity on a string containing a stray
            # literal "{{model}}" is meaningless noise -- skip it below.
            continue

        if len(word_set(en_value)) < MIN_WORDS_FOR_AUTO_CHECK:
            findings.append(
                Finding(key=key, kind="too_short", english=en_value, translated=target_value)
            )
            continue

        result = translator.translate(target_value, source_lang=f"{lang}-IN", target_lang="en-IN")
        back = result.translated_text
        ratio = word_overlap(en_value, back)
        if ratio < SIMILARITY_FLOOR:
            findings.append(
                Finding(
                    key=key,
                    kind="low_similarity",
                    english=en_value,
                    translated=target_value,
                    back_translated=back,
                    similarity=round(ratio, 2),
                )
            )
        # Sarvam rejects some requests fired back-to-back with no spacing
        # (see translate_ui_strings.py) -- same small pause here.
        time.sleep(0.3)

    return findings


def write_report(lang: str, findings: list[Finding], total_keys: int) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"{lang}.md"
    lines = [
        f"# Translation QA -- {lang}.json",
        f"{total_keys} strings checked, {len(findings)} flagged.",
        "",
    ]
    by_kind: dict[str, list[Finding]] = {}
    for f in findings:
        by_kind.setdefault(f.kind, []).append(f)

    if "missing_key" in by_kind:
        lines.append(f"## Missing keys ({len(by_kind['missing_key'])})")
        for f in by_kind["missing_key"]:
            lines.append(f"- `{f.key}`: en=\"{f.english}\"")
        lines.append("")

    if "placeholder_mismatch" in by_kind:
        lines.append(f"## Broken placeholders ({len(by_kind['placeholder_mismatch'])})")
        for f in by_kind["placeholder_mismatch"]:
            lines.append(
                f"- `{f.key}` missing {f.missing_placeholders}: "
                f"en=\"{f.english}\" | translated=\"{f.translated}\""
            )
        lines.append("")

    if "low_similarity" in by_kind:
        lines.append(f"## Low back-translation similarity ({len(by_kind['low_similarity'])})")
        for f in sorted(by_kind["low_similarity"], key=lambda x: x.similarity):
            lines.append(f"- `{f.key}` (similarity {f.similarity})")
            lines.append(f"  - en: \"{f.english}\"")
            lines.append(f"  - {lang}: \"{f.translated}\"")
            lines.append(f"  - back-translated: \"{f.back_translated}\"")
        lines.append("")

    if "too_short" in by_kind:
        lines.append(
            f"## Too short to auto-verify -- read these directly ({len(by_kind['too_short'])})"
        )
        for f in by_kind["too_short"]:
            lines.append(f"- `{f.key}`: en=\"{f.english}\" | {lang}=\"{f.translated}\"")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main() -> None:
    langs = sys.argv[1:] or ALL_LANGS
    en = json.loads((I18N_DIR / "en.json").read_text(encoding="utf-8"))
    flat_en = flatten(en)
    print(f"{len(flat_en)} strings in en.json. Checking: {', '.join(langs)}\n")

    translator = get_translator()

    summary = []
    for lang in langs:
        print(f"--- {lang} ---")
        findings = check_language(lang, flat_en, translator)
        report_path = write_report(lang, findings, len(flat_en))
        missing = sum(1 for f in findings if f.kind == "missing_key")
        broken = sum(1 for f in findings if f.kind == "placeholder_mismatch")
        low_sim = sum(1 for f in findings if f.kind == "low_similarity")
        print(
            f"  missing_key={missing} placeholder_mismatch={broken} "
            f"low_similarity={low_sim} -> {report_path}"
        )
        summary.append((lang, missing, broken, low_sim))

    print("\n=== Summary ===")
    for lang, missing, broken, low_sim in summary:
        print(f"{lang}: {missing} missing, {broken} broken placeholders, {low_sim} low-similarity")


if __name__ == "__main__":
    main()
